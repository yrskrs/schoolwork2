"""Transport limits never silently discard a page, object, pixel or chosen model."""
import base64
import io
import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, SimpleTestCase
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from .ai_payload import encode_payload, optimize_media, image_signature, groq_output_budget, pack_document_pages
from .ai_context import compact_reference_material, focus_assigned_material, media_for_provider
from .gemini_service import _http_post_json, _raw_call_ai_api, call_ai_api, _read_api_body, test_ai_connection as check_connection


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
    def test_pdf_pages_pack_without_changing_a_single_decoded_pixel(self):
        pages=[dict(picture(color,size),document_id='same-pdf',page_number=i+1,page_count=2)
               for i,(color,size) in enumerate([('red',(128,96)),('blue',(90,100))])]
        result=pack_document_pages(pages)
        self.assertEqual(len(result),1)
        sheet=Image.open(io.BytesIO(base64.b64decode(result[0]['data'])))
        for page,box in zip(pages,[(0,0,128,96),(144,0,234,100)]):
            original=Image.open(io.BytesIO(base64.b64decode(page['data'])))
            self.assertEqual(image_signature(sheet.crop(box)),image_signature(original))
        self.assertEqual(result[0]['packed_pages'],[1,2])
        self.assertIn('ліворуч і праворуч',result[0]['source'])
        self.assertEqual(len(pages),2)

    def test_photos_different_documents_alpha_and_oversized_pages_are_not_packed(self):
        a=picture();b=picture('blue')
        self.assertEqual(pack_document_pages([a,b]),[a,b])
        a=dict(a,document_id='first',page_number=1)
        b=dict(b,document_id='second',page_number=2)
        self.assertEqual(pack_document_pages([a,b]),[a,b])
        b=dict(picture(alpha=True),document_id='first',page_number=2)
        self.assertEqual(pack_document_pages([a,b]),[a,b])
        huge=[dict(picture(size=(2000,2000)),document_id='large',page_number=i) for i in [1,2]]
        self.assertEqual(pack_document_pages(huge),huge)

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
    @patch('feed.gemini_service._http_post_json',return_value=(200,{'error':{'code':503,'message':'backend unavailable'}},''))
    def test_http_200_error_is_reported_as_actual_backend_failure(self,http):
        status,reply,error,data=_raw_call_ai_api('test',provider='openrouter',model_name='openrouter/free')
        self.assertEqual(status,503)
        self.assertIsNone(reply)
        self.assertIn('backend unavailable',error)
        self.assertEqual(data['_schoolnet_http_status'],200)

    @patch('feed.gemini_service._http_post_json',return_value=(200,{'choices':[{'finish_reason':'error','message':{'content':'{"suggested_grade":12}'},'error':{'code':502,'message':'disconnected'}}]},''))
    def test_partial_grade_is_never_accepted_when_choice_contains_error(self,http):
        result=_raw_call_ai_api('test',provider='openrouter',model_name='chosen')
        self.assertEqual(result[0],502)
        self.assertIsNone(result[1])
        self.assertIn('disconnected',result[2])

    @patch('feed.gemini_service._http_post_json')
    def test_content_filter_and_refusal_are_not_misreported_as_empty_answers(self,http):
        for choice in [{'finish_reason':'content_filter','message':{'content':None}},
                       {'finish_reason':'stop','message':{'content':None,'refusal':'policy'}}]:
            http.return_value=(200,{'choices':[choice]},'')
            result=_raw_call_ai_api('test',provider='openrouter',model_name='openrouter/free')
            self.assertEqual(result[0],403)
            self.assertIn('відмовив',result[2])

    @patch('feed.gemini_service._raw_call_ai_api')
    def test_empty_length_and_timeout_are_failed_metrics_not_successful_http_200(self,raw):
        from .models import AIRequestLog
        for code,reply,error in [(200,'','MAX_TOKENS'),(200,'','Порожня відповідь'),(0,None,'timeout')]:
            raw.return_value=(code,reply,error,{})
            call_ai_api('test',provider='openrouter',model_name='openrouter/free')
            metric=AIRequestLog.objects.latest('pk')
            self.assertFalse(metric.is_success)
            self.assertEqual(metric.status_code,code)

    @patch('feed.gemini_service._http_post_json',return_value=(200,{'choices':[{'finish_reason':'length','message':{'content':None}}],'model':'actual-model','usage':{'completion_tokens_details':{'reasoning_tokens':5380}}},''))
    def test_exhausted_reasoning_budget_has_specific_diagnostic(self,http):
        result=_raw_call_ai_api('test',provider='openrouter',model_name='openrouter/free',thinking_budget=0)
        self.assertEqual(http.call_args.args[1]['reasoning'],{'enabled':False})
        self.assertIn('actual-model',result[2])
        self.assertIn('5380',result[2])

    @patch('feed.gemini_service.time.monotonic',side_effect=[1,31])
    def test_whitespace_keepalive_cannot_extend_total_response_deadline(self,clock):
        response=MagicMock()
        response.read1.side_effect=[b'\n ',b'\n ']
        with self.assertRaises(TimeoutError):
            _read_api_body(response,30)

    @patch('feed.gemini_service.time.monotonic',return_value=1)
    def test_chunked_body_is_parsed_without_losing_json_or_unicode(self,clock):
        response=MagicMock()
        response.read1.side_effect=[b'\n ',b'{"reply":"', 'так'.encode(),b'"}',b'']
        self.assertEqual(json.loads(_read_api_body(response,30)),{'reply':'так'})

    def test_qwen_budget_accounts_for_documented_2048_tokens_per_image(self):
        model='qwen/qwen3.8-27b'
        self.assertEqual(groq_output_budget(model,'а'*2200,'',[{'source':''}],6000),3602)

    @patch('feed.gemini_service._http_post_json',return_value=(200,{'choices':[{'message':{'content':'OK'}}]},''))
    def test_groq_large_estimate_is_not_a_fabricated_local_413(self,http):
        result = _raw_call_ai_api('Важливий текст учня. ' * 1600, provider='groq', model_name='openai/gpt-oss-120b')
        self.assertEqual(result[0],200)
        http.assert_called_once()
        payload = http.call_args.args[1]
        self.assertEqual(payload['messages'][-1]['content'], 'Важливий текст учня. ' * 1600)
        self.assertGreaterEqual(payload['max_completion_tokens'],3500)
        self.assertNotIn('max_tokens',payload)

    def test_groq_keeps_enough_output_for_complete_assessment_even_near_input_target(self):
        budget = groq_output_budget('openai/gpt-oss-120b','а'*11000,'',[],5380)
        self.assertEqual(budget,3500)
        self.assertEqual(groq_output_budget('openai/gpt-oss-120b','а'*30000,'',[],5380),3500)
        self.assertEqual(groq_output_budget('qwen/qwen3.8-27b','а'*10000,'',[{'source':''}],3570),3500)
        self.assertEqual(groq_output_budget('qwen/qwen3.8-27b','а'*10000,'',[{'source':''}],1000),1000)
        self.assertEqual(groq_output_budget('custom-model','а'*30000,'',[],5380),5380)

    @patch('feed.gemini_service._http_post_json',return_value=(200,{'choices':[{'message':{'content':'OK'}}]},''))
    @patch('feed.ai_context.media_for_provider',side_effect=lambda items,provider: items)
    def test_readable_reference_media_removed_before_conversion_but_required_sources_remain(self,convert,http):
        student = picture()
        task = dict(picture('blue'),evidence_role='task',text_available=True)
        reference = dict(picture('green'),evidence_role='reference',text_available=True)
        unreadable = dict(picture('black'),evidence_role='reference',text_available=False)
        items=[student,task,reference,unreadable]
        _raw_call_ai_api('Full text',provider='openrouter',model_name='chosen',inline_media=items)
        self.assertEqual(convert.call_args.args[0],[student,task,unreadable])
        self.assertEqual(len(items),4)

    @patch('feed.gemini_service._http_post_json',return_value=(200,{'choices':[{'message':{'content':'OK'}}]},''))
    @patch('feed.ai_context.media_for_provider',return_value=[picture()])
    def test_free_router_uses_pdf_pages_without_native_plugin(self,convert,http):
        pdf=document(picture())
        _raw_call_ai_api('Read',provider='openrouter',model_name='openrouter/free',inline_media=[pdf])
        self.assertEqual(convert.call_args.args[1],'openai')
        self.assertNotIn('plugins',http.call_args.args[1])
        self.assertEqual(http.call_args.args[1]['model'],'openrouter/free')

    @patch('feed.gemini_service._http_post_json')
    def test_reasoning_alone_is_not_an_assessment(self,http):
        http.return_value=(200,{'choices':[{'message':{'content':None,'reasoning_content':'private reasoning'}}]},'')
        result=_raw_call_ai_api('test',provider='openrouter',model_name='chosen')
        self.assertEqual(result[1],'')
        self.assertIn('Порожня',result[2])
        http.return_value=(200,{'choices':[{'message':{'content':[{'type':'text','text':'{"suggested_grade":8}'}]}}]},'')
        result=_raw_call_ai_api('test',provider='openrouter',model_name='chosen')
        self.assertEqual(result[1],'{"suggested_grade":8}')

    @patch('feed.gemini_service._http_post_json',return_value=(200,{'choices':[{'message':{'content':'OK'}}]},''))
    def test_groq_reasoning_controls_follow_fast_and_thinking_modes(self,http):
        for model,effort in [('qwen/qwen3.8-27b','none'),('openai/gpt-oss-120b','low')]:
            _raw_call_ai_api('test',provider='groq',model_name=model,thinking_budget=0)
            self.assertEqual(http.call_args.args[1]['reasoning_effort'],effort)
            _raw_call_ai_api('test',provider='groq',model_name=model,thinking_budget=None)
            self.assertNotIn('reasoning_effort',http.call_args.args[1])

    def test_compact_utf8_json_roundtrip_preserves_program_and_unicode(self):
        payload={'messages':[{'content':'Карта знань\n  if x:\n    return "так"'}]}
        compact=encode_payload(payload)
        self.assertEqual(json.loads(compact),payload)
        self.assertLess(len(compact),len(json.dumps(payload).encode()))

    @patch('feed.gemini_service._get_http_pool')
    def test_pool_sends_identified_user_agent_and_compact_bytes(self,pool):
        response=pool.return_value.request.return_value=MagicMock(status=200)
        response.read1.side_effect=[b'{}',b'']
        _http_post_json('https://api.groq.com/openai/v1/chat/completions',{'model':'chosen'},headers={'Authorization':'Bearer secret'})
        kwargs=pool.return_value.request.call_args.kwargs
        self.assertTrue(kwargs['headers']['User-Agent'].startswith('SchoolNet/'))
        self.assertEqual(kwargs['headers']['Authorization'],'Bearer secret')

    @patch('feed.gemini_service.urllib.request.urlopen')
    @patch('feed.gemini_service._get_http_pool',return_value=False)
    def test_fallback_sends_same_identified_user_agent(self,pool,urlopen):
        response=urlopen.return_value.__enter__.return_value
        response.getcode.return_value=200;response.read1.side_effect=[b'{}',b'']
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


class FocusedMaterialTests(SimpleTestCase):
    def test_exact_assigned_exercise_is_complete_with_common_instructions(self):
        text='Спільні правила.\nЗавдання 1. Інша вправа.\nЗавдання 6. Початок.\n'+'Деталі. '*100+'\nЗавдання 7. Незадана вправа.'
        focused,selected=focus_assigned_material(text,[6])
        self.assertTrue(selected)
        self.assertIn('Спільні правила.',focused)
        self.assertIn(('Деталі. '*100).strip(),focused)
        self.assertNotIn('Інша вправа.',focused)
        self.assertNotIn('Незадана вправа.',focused)

    def test_ambiguous_or_dependent_task_keeps_full_source(self):
        for text,numbers in [('Вправа 1. A\nВправа 2. Використай результат завдання 1.',[2]),
                             ('Вправа 1. A\nВправа 2. B',[3]),
                             ('Вправа 1. A\nВправа 2. B\nКритерії оцінювання: 12 балів.',[1])]:
            self.assertEqual(focus_assigned_material(text,numbers),(text,False))

    def test_flattened_office_table_retains_only_assigned_number(self):
        text='Матеріал: Завдання 1. Інше. Завдання 6. Повна умова. Завдання 7. Інше.'
        focused,selected=focus_assigned_material(text,[6])
        self.assertTrue(selected)
        self.assertIn('Завдання 6. Повна умова.',focused)
        self.assertNotIn('Завдання 7.',focused)

    def test_late_criteria_and_task_continuations_survive_reference_compaction(self):
        text='Теорія. '*600+'\n\nЗавдання: початок.\nПовна умова.\n\nКритерії: особлива розбаловка.'
        result=compact_reference_material(text,theory_budget=0)
        self.assertIn('Повна умова.',result)
        self.assertIn('особлива розбаловка',result)
        self.assertNotIn('Теорія.',result)

    def test_inflected_task_keywords_after_300_chars_of_slide_are_preserved(self):
        slides=''.join(f'📽️ Слайд {i}\n'+('Теорія. '*60 if i==5 else 'Теорія.')+
                       ('\nВиконайте домашнє завдання: створіть листівку.' if i==5 else '')+'\n' for i in range(1,7))
        result=compact_reference_material(slides)
        self.assertIn('створіть листівку.',result)

    def test_reference_exercises_are_filtered_only_when_primary_task_is_resolved(self):
        slides=''.join(f'📽️ Слайд {i}\nВправа {i}\nПовна умова вправи номер {i}.\n' for i in range(1,9))
        result=compact_reference_material(slides,theory_budget=0,task_numbers=[6],task_source_resolved=True)
        self.assertIn('Повна умова вправи номер 6.',result)
        self.assertNotIn('Повна умова вправи номер 7.',result)
        unresolved=compact_reference_material(slides,theory_budget=0,task_numbers=[6],task_source_resolved=False)
        self.assertIn('Повна умова вправи номер 7.',unresolved)
