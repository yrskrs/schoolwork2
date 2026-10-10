import io
import zipfile
from pathlib import Path
from PIL import Image, PngImagePlugin
from django.conf import settings
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from feed.models import Assignment, ClassGroup, Subject, Submission, Teacher
from feed.ai_provenance import file_provenance, normalize_authorship


def make_png_with_text(keyword, text):
    img = Image.new('RGB', (16, 16), color='white')
    meta = PngImagePlugin.PngInfo()
    meta.add_text(keyword, text)
    buf = io.BytesIO()
    img.save(buf, format='PNG', pnginfo=meta)
    return buf.getvalue()


class AIDetectionFormatsTests(TestCase):
    def setUp(self):
        import tempfile
        self.test_media = tempfile.TemporaryDirectory(prefix='schoolwork-format-tests-')
        self.addCleanup(self.test_media.cleanup)
        media_override = override_settings(MEDIA_ROOT=Path(self.test_media.name))
        media_override.enable()
        self.addCleanup(media_override.disable)
        User = get_user_model()
        self.teacher_user = User.objects.create_user(username='teacher_det', password='passWord123!')
        self.teacher_profile = Teacher.objects.create(user=self.teacher_user, full_name='Вчитель Тест')
        self.class_group = ClassGroup.objects.create(name='9-А', grade=9, letter='А')
        self.subject = Subject.objects.create(name='Інформатика')
        self.assignment = Assignment.objects.create(
            teacher=self.teacher_profile,
            subject=self.subject,
            title='Презентація про ШІ',
            allow_ai_usage=False
        )
        self.assignment.classes.add(self.class_group)
        self.student = User.objects.create_user(username='student_det', password='passWord123!')
        self.media_dir = Path(settings.MEDIA_ROOT)
        self.media_dir.mkdir(parents=True, exist_ok=True)

    def test_pptx_gamma_app_xml_detection(self):
        pptx_path = self.media_dir / 'gamma_presentation.pptx'
        app_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
            '<Application>Gamma Presentation App</Application>'
            '</Properties>'
        )
        with zipfile.ZipFile(pptx_path, 'w') as zf:
            zf.writestr('docProps/app.xml', app_xml.encode('utf-8'))
            zf.writestr('ppt/slides/slide1.xml', b'<p:sld><p:sp><a:p><a:r><a:t>Theme Title</a:t></a:r></a:p></p:sp></p:sld>')

        prov = file_provenance(pptx_path)
        signals = prov.get('signals', [])
        self.assertTrue(len(signals) > 0, "Should detect Gamma AI generator in PPTX app.xml")
        self.assertEqual(signals[0]['basis'], 'metadata')
        self.assertEqual(signals[0]['location'], 'docProps/app.xml')
        self.assertIn('Gamma', signals[0]['observation'])

    def test_pptx_slide_ai_marker_detection(self):
        pptx_path = self.media_dir / 'slide_markers.pptx'
        slide_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            '<p:sp><a:p><a:r><a:t>Ось варіант слайду, який я підготував як штучний інтелект для вас.</a:t></a:r></p:sp>'
            '</p:sld>'
        )
        with zipfile.ZipFile(pptx_path, 'w') as zf:
            zf.writestr('ppt/slides/slide1.xml', slide_xml.encode('utf-8'))

        prov = file_provenance(pptx_path)
        signals = prov.get('signals', [])
        self.assertTrue(len(signals) > 0, "Should detect slide AI text marker in slide1.xml")
        slide_signal = [s for s in signals if s.get('basis') == 'text_analysis']
        self.assertTrue(len(slide_signal) > 0)
        self.assertEqual(slide_signal[0]['location'], 'Слайд 1')
        self.assertIn('як штучний інтелект', slide_signal[0]['observation'])

    def test_docx_chatgpt_generator_metadata_detection(self):
        docx_path = self.media_dir / 'generated_doc.docx'
        core_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:creator>ChatGPT / OpenAI</dc:creator>'
            '</cp:coreProperties>'
        )
        with zipfile.ZipFile(docx_path, 'w') as zf:
            zf.writestr('docProps/core.xml', core_xml.encode('utf-8'))
            zf.writestr('word/document.xml', b'<w:document><w:body><w:p><w:r><w:t>Essay</w:t></w:r></w:p></w:body></w:document>')

        prov = file_provenance(docx_path)
        signals = prov.get('signals', [])
        self.assertTrue(len(signals) > 0, "Should detect ChatGPT creator in docProps/core.xml")
        self.assertEqual(signals[0]['basis'], 'metadata')
        self.assertEqual(signals[0]['location'], 'docProps/core.xml')

    def test_pdf_metadata_ai_detection(self):
        pdf_path = self.media_dir / 'chatgpt_export.pdf'
        content = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<< /Title (AI Presentation) /Creator (Canva AI Presentation Generator) /Producer (ChatGPT) >>\nendobj\n"
            b"xref\n0 2\n0000000000 65535 f\n0000000010 00000 n\ntrailer\n<< /Size 2 /Root 1 0 R >>\nstartxref\n110\n%%EOF"
        )
        pdf_path.write_bytes(content)

        prov = file_provenance(pdf_path)
        signals = prov.get('signals', [])
        self.assertTrue(len(signals) > 0, "Should detect Canva / ChatGPT in PDF metadata")
        self.assertEqual(signals[0]['basis'], 'metadata')

    def test_image_xmp_algorithmic_media_detection(self):
        img_path = self.media_dir / 'ai_photo.png'
        # PNG with XMP tag trainedAlgorithmicMedia
        xmp_xml = '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description xmlns:iptc="http://iptc.org/std/Iptc4xmpExt/2008-02-20/"><iptc:DigitalSourceType>http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia</iptc:DigitalSourceType></rdf:Description></rdf:RDF></x:xmpmeta>'
        img_bytes = make_png_with_text('XML:com.adobe.xmp', xmp_xml)
        img_path.write_bytes(img_bytes)

        prov = file_provenance(img_path)
        signals = prov.get('signals', [])
        self.assertTrue(len(signals) > 0, "Should detect trainedAlgorithmicMedia XMP in image")
        self.assertEqual(signals[0]['basis'], 'metadata')
        self.assertIn('trainedAlgorithmicMedia', signals[0]['observation'])

    def test_normalize_authorship_preserves_presentation_ai_result(self):
        # Even if status is 'none' or 'unknown', high ai_percent and slide evidence must be preserved!
        gemini_result = {
            'ai_generated_detected': True,
            'ai_generated_percent': 85,
            'ai_generated_confidence': 'high',
            'ai_generated_details': 'Виявлено ознаки генерації слайдів сервісом Gamma та стандартні формулювання ШІ.',
            'ai_authorship_analysis': {
                'status': 'none',
                'evidence': [
                    {
                        'source': 'Презентація',
                        'location': 'Слайд 2',
                        'basis': 'slide_content',
                        'observation': 'Слайд 2 містить шаблонний текст: "Ось презентація на тему..."'
                    }
                ]
            }
        }
        res = normalize_authorship(gemini_result, False)
        self.assertTrue(res['ai_generated_detected'], "Detection must NOT be wiped when evidence exists")
        self.assertEqual(res['ai_generated_percent'], 85)
        self.assertEqual(res['ai_generated_confidence'], 'high')
        self.assertIn('Слайд 2', res['ai_generated_details'])

    def test_batch_queue_api_includes_ai_detection_fields(self):
        from feed.views import api_ai_get_batch_queue
        from django.test import RequestFactory
        import json

        sub = Submission.objects.create(
            assignment=self.assignment,
            teacher=self.teacher_profile,
            first_name='Олександр',
            last_name='Шевченко',
            class_group=self.class_group,
            ai_status='success',
            ai_suggested_grade='7',
            ai_generated_detected=True,
            ai_generated_percent=90,
            ai_generated_confidence='high',
            ai_generated_details='Згенеровано через Tome AI.'
        )

        rf = RequestFactory()
        req = rf.post('/api/ai/get-batch-queue/', {'class_id': str(self.class_group.id)})
        req.user = self.teacher_user

        response = api_ai_get_batch_queue(req)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['queue']), 1)
        item = data['queue'][0]
        self.assertTrue(item['ai_generated_detected'])
        self.assertEqual(item['ai_generated_percent'], 90)
        self.assertIn('Tome AI', item['ai_generated_details'])

    def test_pptx_chatgpt_prompt_placeholders(self):
        pptx_path = self.media_dir / 'chatgpt_outline.pptx'
        slide_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            '<p:sp><a:p><a:r><a:t>Слайд 1: Вступ до теми</a:t></a:r></p:sp>'
            '<p:sp><a:p><a:r><a:t>[Зображення: концепція штучного інтелекту в освіті]</a:t></a:r></p:sp>'
            '</p:sld>'
        )
        with zipfile.ZipFile(pptx_path, 'w') as zf:
            zf.writestr('ppt/slides/slide1.xml', slide_xml.encode('utf-8'))

        prov = file_provenance(pptx_path)
        signals = prov.get('signals', [])
        self.assertTrue(len(signals) > 0, "Should detect ChatGPT prompt placeholder in PPTX slide")
        self.assertEqual(signals[0]['location'], 'Слайд 1')
        self.assertEqual(signals[0]['basis'], 'text_analysis')

    def test_pptx_speaker_notes_ai_detection(self):
        pptx_path = self.media_dir / 'notes_generator.pptx'
        slide_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            '<p:sp><a:p><a:r><a:t>Чистий заголовок</a:t></a:r></p:sp>'
            '</p:sld>'
        )
        notes_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<p:notes xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            '<p:sp><a:p><a:r><a:t>Згенеровано за допомогою ChatGPT для доповіді на уроці.</a:t></a:r></p:sp>'
            '</p:notes>'
        )
        with zipfile.ZipFile(pptx_path, 'w') as zf:
            zf.writestr('ppt/slides/slide1.xml', slide_xml.encode('utf-8'))
            zf.writestr('ppt/notesSlides/notesSlide1.xml', notes_xml.encode('utf-8'))

        prov = file_provenance(pptx_path)
        signals = prov.get('signals', [])
        notes_sig = [s for s in signals if 'Нотатки' in s.get('location', '')]
        self.assertTrue(len(notes_sig) > 0, "Should detect AI in speaker notes")
        self.assertIn('ChatGPT', notes_sig[0]['observation'])

    def test_pptx_comments_generator_detection(self):
        pptx_path = self.media_dir / 'comments_canva.pptx'
        comment_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<p:cmList xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            '<p:cm authorId="0"><p:text>Design created with Canva AI Presentation</p:text></p:cm>'
            '</p:cmList>'
        )
        with zipfile.ZipFile(pptx_path, 'w') as zf:
            zf.writestr('ppt/slides/slide1.xml', b'<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:sp><a:p><a:r><a:t>Title</a:t></a:r></p:sp></p:sld>')
            zf.writestr('ppt/comments/comment1.xml', comment_xml.encode('utf-8'))

        prov = file_provenance(pptx_path)
        signals = prov.get('signals', [])
        self.assertTrue(len(signals) > 0, "Should detect Canva generator in PPTX comments")
        self.assertTrue(any('Canva' in s['observation'] for s in signals))

    def test_odp_slide_ai_marker_detection(self):
        odp_path = self.media_dir / 'odp_marker.odp'
        content_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
            'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
            '<office:body><office:presentation>'
            '<draw:page draw:name="page1">'
            '<draw:frame><text:p>Вступ до теми</text:p></draw:frame>'
            '</draw:page>'
            '<draw:page draw:name="page2">'
            '<draw:frame><text:p>Створено за допомогою ШІ для захисту проєкту</text:p></draw:frame>'
            '</draw:page>'
            '</office:presentation></office:body>'
            '</office:document-content>'
        )
        with zipfile.ZipFile(odp_path, 'w') as zf:
            zf.writestr('content.xml', content_xml.encode('utf-8'))

        prov = file_provenance(odp_path)
        signals = prov.get('signals', [])
        self.assertTrue(len(signals) > 0, "Should detect AI marker in ODP slide")
        slide2_sig = [s for s in signals if s.get('location') == 'Слайд 2']
        self.assertTrue(len(slide2_sig) > 0, "Signal must indicate Slide 2 specifically")

    def test_odp_notes_ai_detection(self):
        odp_path = self.media_dir / 'odp_notes.odp'
        content_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
            'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
            'xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0">'
            '<office:body><office:presentation>'
            '<draw:page draw:name="page1">'
            '<draw:frame><text:p>Звичайний заголовок</text:p></draw:frame>'
            '<presentation:notes><text:p>Created with Gamma App</text:p></presentation:notes>'
            '</draw:page>'
            '</office:presentation></office:body>'
            '</office:document-content>'
        )
        with zipfile.ZipFile(odp_path, 'w') as zf:
            zf.writestr('content.xml', content_xml.encode('utf-8'))

        prov = file_provenance(odp_path)
        signals = prov.get('signals', [])
        notes_sig = [s for s in signals if 'Нотатки' in s.get('location', '')]
        self.assertTrue(len(notes_sig) > 0, "Should detect Gamma in ODP presenter notes")
        self.assertIn('Gamma', notes_sig[0]['observation'])

    def test_binary_ppt_ai_marker_detection(self):
        ppt_path = self.media_dir / 'legacy_presentation.ppt'
        # Emulate binary PowerPoint containing UTF-16LE text
        text = "Створено за допомогою ChatGPT для доповіді"
        data = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + text.encode("utf-16le") + b"\x00" * 32
        ppt_path.write_bytes(data)

        prov = file_provenance(ppt_path)
        signals = prov.get('signals', [])
        self.assertTrue(len(signals) > 0, "Should detect AI in binary .ppt file")
        self.assertIn('.ppt', signals[0]['location'])

    def test_extract_text_from_odp_presentation_structure(self):
        from feed.gemini_service import extract_text_from_odp_presentation
        odp_path = self.media_dir / 'odp_structure.odp'
        content_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
            'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
            'xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0">'
            '<office:body><office:presentation>'
            '<draw:page draw:name="page1">'
            '<draw:frame><text:h>Тема: Екологія планети</text:h></draw:frame>'
            '<draw:frame><text:p>Головні чинники впливу на довкілля</text:p></draw:frame>'
            '</draw:page>'
            '<draw:page draw:name="page2">'
            '<draw:frame><text:h>Висновки</text:h></draw:frame>'
            '<draw:frame><text:p>Необхідно сортувати відходи</text:p></draw:frame>'
            '<presentation:notes><text:p>Звернути увагу слухачів на графік</text:p></presentation:notes>'
            '</draw:page>'
            '</office:presentation></office:body>'
            '</office:document-content>'
        )
        with zipfile.ZipFile(odp_path, 'w') as zf:
            zf.writestr('content.xml', content_xml.encode('utf-8'))

        text = extract_text_from_odp_presentation(str(odp_path))
        self.assertIn('Всього слайдів у презентації: 2', text)
        self.assertIn('📽️ Слайд 1/2: «Тема: Екологія планети»', text)
        self.assertIn('📽️ Слайд 2/2: «Висновки»', text)
        self.assertIn('[Нотатки доповідача: Звернути увагу слухачів на графік]', text)

