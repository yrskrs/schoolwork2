"""Transport limits never silently discard a page, object, pixel or chosen model."""
import base64
import io
import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, SimpleTestCase
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from .ai_payload import encode_payload, optimize_media, image_signature
from .gemini_service import _http_post_json, _raw_call_ai_api, call_ai_api, test_ai_connection as check_connection


def picture(color='red', size=(128,96), alpha=False):
    buffer=io.BytesIO()
    Image.new('RGBA' if alpha else 'RGB',size,color).save(buffer,format='PNG')
    return {'mime_type':'image/png','data':base64.b64encode(buffer.getvalue()).decode(),'source':'Карта знань учня'}


def document(image, case='visible'):
    buffer=io.BytesIO()
    pdf=canvas.Canvas(buffer,pagesize=(256,192))
    if case=='clipped':
        path=pdf.beginPath();path.rect(0,0,128,192);pdf.clipPath(path,stroke=0)
    if case=='transparent':
        pdf.setFillAlpha(.5)
    pdf.drawImage(ImageReader(io.BytesIO(base64.b64decode(image['data']))),
                  -100 if case=='outside' else 0,0,8 if case=='small' else 256,8 if case=='small' else 192,mask='auto')
    if case=='covered':
        pdf.setFillColorRGB(1,1,1);pdf.rect(0,0,256,192,fill=1,stroke=0)
    if case=='text-overlay':
        pdf.drawString(20,20,'A later overlay')
    pdf.showPage();pdf.save()
    return {'mime_type':'application/pdf','data':base64.b64encode(buffer.getvalue()).decode(),'source':'Повні сторінки роботи'}


class MediaPayloadTests(SimpleTestCase):
    def test_lossless_compression_keeps_resolution_pixels_and_alpha(self):
        original=picture((25,50,75,70),(700,500),alpha=True)
        result=optimize_media([original],'openrouter')[0]
        before=Image.open(io.BytesIO(base64.b64decode(original['data'])))
        after=Image.open(io.BytesIO(base64.b64decode(result['data'])))
        self.assertEqual(before.size,after.size)
        self.assertEqual(image_signature(before),image_signature(after))
        self.assertLessEqual(len(result['data']),len(original['data']))

    def test_exact_duplicates_keep_both_source_labels(self):
        first=picture();second=dict(first,source='Та сама карта в матеріалі вчителя')
        result=optimize_media([first,second],'openrouter')
        self.assertEqual(len(result),1)
        self.assertIn(first['source'],result[0]['source'])
        self.assertIn(second['source'],result[0]['source'])
        self.assertEqual(first['source'],'Карта знань учня')

    def test_images_with_different_pixels_or_sizes_are_not_combined(self):
        items=[picture('red'),picture('blue'),picture('red',(129,96))]
        self.assertEqual(len(optimize_media(items,'openrouter')),3)

    def test_full_visible_pdf_image_is_sent_once_with_all_source_labels(self):
        image=picture();pdf=document(image)
        for items in [[pdf,image],[image,pdf]]:
            result=optimize_media(items,'gemini')
            self.assertEqual(len(result),1)
            self.assertEqual(result[0]['data'],pdf['data'])
            self.assertIn(image['source'],result[0]['source'])

    def test_uncertain_or_partial_pdf_view_keeps_independent_original(self):
        image=picture()
        for case in ['clipped','outside','small','transparent','covered','text-overlay']:
            with self.subTest(case=case):
                self.assertEqual(len(optimize_media([document(image,case),image],'gemini')),2)

    def test_non_native_pdf_provider_keeps_original_even_when_pdf_matches(self):
        image=picture()
        self.assertEqual(len(optimize_media([document(image),image],'openrouter')),2)

    def test_pdf_transparent_image_does_not_hide_alpha_original(self):
        image=picture((25,50,75,70),alpha=True)
        self.assertEqual(len(optimize_media([document(image),image],'gemini')),2)

    def test_corrupt_pdf_does_not_remove_images(self):
        pdf={'mime_type':'application/pdf','data':base64.b64encode(b'not a PDF').decode()}
        self.assertEqual(len(optimize_media([pdf,picture()],'gemini')),2)

    def test_unsupported_local_image_codec_preserves_original(self):
        original={'mime_type':'image/jpeg','data':base64.b64encode(b'opaque-image-data').decode(),'source':'Original'}
        self.assertEqual(optimize_media([original],'gemini'),[original])

    def test_animated_images_are_not_reencoded_or_deduplicated_by_first_frame(self):
        frames=[Image.new('RGB',(30,20),color) for color in ['red','blue','green']]
        items=[]
        for second in frames[1:]:
            buf=io.BytesIO();frames[0].save(buf,format='GIF',save_all=True,append_images=[second],duration=100,loop=0)
            items.append({'mime_type':'image/gif','data':base64.b64encode(buf.getvalue()).decode()})
        result=optimize_media(items,'gemini')
        self.assertEqual([item['data'] for item in result],[item['data'] for item in items])


class TransportTests(TestCase):
    def test_compact_utf8_json_roundtrip_preserves_program_and_unicode(self):
        payload={'messages':[{'content':'Карта знань\n  if x:\n    return "так"'}]}
        compact=encode_payload(payload)
        self.assertEqual(json.loads(compact),payload)
        self.assertLess(len(compact),len(json.dumps(payload).encode()))

    @patch('feed.gemini_service._get_http_pool')
    def test_pool_sends_identified_user_agent_and_compact_bytes(self,pool):
        pool.return_value.request.return_value=MagicMock(status=200,data=b'{}')
        _http_post_json('https://api.groq.com/openai/v1/chat/completions',{'model':'chosen'},headers={'Authorization':'Bearer secret'})
        kwargs=pool.return_value.request.call_args.kwargs
        self.assertTrue(kwargs['headers']['User-Agent'].startswith('SchoolNet/'))
        self.assertEqual(kwargs['headers']['Authorization'],'Bearer secret')

    @patch('feed.gemini_service.urllib.request.urlopen')
    @patch('feed.gemini_service._get_http_pool',return_value=False)
    def test_fallback_sends_same_identified_user_agent(self,pool,urlopen):
        response=urlopen.return_value.__enter__.return_value
        response.getcode.return_value=200;response.read.return_value=b'{}'
        _http_post_json('https://api.groq.com/openai/v1/chat/completions',{'model':'chosen'})
        req=urlopen.call_args.args[0]
        self.assertTrue(req.get_header('User-agent').startswith('SchoolNet/'))

    @patch('feed.gemini_service._get_http_pool')
    @patch('feed.ai_payload.OPENROUTER_MAX_REQUEST_BYTES',200)
    def test_final_serialized_size_is_checked_before_network(self,pool):
        status,body,message=_http_post_json('https://openrouter.ai/api/v1/chat/completions',{'text':'large'*100})
        self.assertEqual(status,413);pool.assert_not_called()
        self.assertIn('не вилучено',message)

    @patch('feed.gemini_service.log_ai_request_metric')
    @patch('feed.ai_payload.OPENROUTER_MAX_REQUEST_BYTES',200)
    def test_locally_blocked_body_does_not_count_as_actual_api_call(self,metric):
        status,*_=call_ai_api('material'*100,provider='openrouter',api_key='secret',model_name='chosen')
        self.assertEqual(status,413);metric.assert_not_called()

    @patch('feed.gemini_service._http_post_json',return_value=(403,{'title':'Access denied','detail':'Cloudflare Error 1010'},'Forbidden'))
    def test_gateway_error_preserved_and_not_mislabeled_as_invalid_key(self,http):
        status,_,error,_=_raw_call_ai_api('test',provider='groq',api_key='secret',model_name='other-model')
        self.assertEqual(status,403);self.assertIn('1010',error)
        self.assertEqual(http.call_args.args[1]['model'],'other-model')

    @patch('feed.gemini_service._http_post_json',return_value=(200,{'choices':[{'message':{'content':'OK'}}]},'OK'))
    def test_openrouter_receives_complete_pdf_without_page_or_ocr_expansion(self,http):
        pdf=document(picture())
        _raw_call_ai_api('Read all pages',provider='openrouter',api_key='secret',model_name='chosen',inline_media=[pdf])
        payload=http.call_args.args[1]
        files=[item for item in payload['messages'][0]['content'] if item['type']=='file']
        self.assertEqual(len(files),1)
        self.assertEqual(files[0]['file']['file_data'],'data:application/pdf;base64,'+pdf['data'])
        self.assertEqual(payload['plugins'][0]['pdf']['engine'],'native')
        self.assertEqual(payload['model'],'chosen')

    @patch('feed.gemini_service.call_ai_api',return_value=(403,None,'project permissions_error',{}))
    def test_permission_denial_keeps_reason_and_selected_model(self,call):
        ok,message,model,provider=check_connection(provider='groq',api_key='secret',model_name='llama-other')
        self.assertFalse(ok);self.assertIn('permissions_error',message)
        self.assertNotIn('Недійсний API Key',message)
        self.assertEqual(model,'llama-other')
        self.assertEqual(call.call_args.kwargs['model_name'],model)

    @patch('feed.gemini_service.call_ai_api',return_value=(401,None,'Invalid secret',{}))
    def test_authentication_reason_redacts_key(self,call):
        ok,message,*_=check_connection(provider='groq',api_key='secret',model_name='chosen')
        self.assertFalse(ok);self.assertIn('401',message);self.assertNotIn('secret',message)
