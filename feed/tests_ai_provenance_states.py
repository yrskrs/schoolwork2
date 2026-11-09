import tempfile
import zipfile
from pathlib import Path

from django.test import SimpleTestCase

from .ai_provenance import file_provenance, normalize_authorship


class AuthorshipStateTests(SimpleTestCase):
    def test_plain_text_without_provenance_is_insufficient(self):
        result = normalize_authorship({'ai_authorship_analysis': {'status': 'none'}}, False)
        self.assertEqual(result['ai_authorship_state'], 'insufficient')
        self.assertFalse(result['ai_generated_detected'])
        self.assertIn('не встановлено', result['ai_generated_details'])

    def test_checked_file_without_signals_is_none(self):
        result = normalize_authorship(
            {'ai_authorship_analysis': {'status': 'none'}}, False,
            provenance=[{'source': 'photo.jpg', 'checked': True, 'signals': [], 'limitations': []}])
        self.assertEqual(result['ai_authorship_state'], 'none')
        self.assertFalse(result['ai_generated_detected'])

    def test_metadata_signal_is_suspected_not_proof(self):
        result = normalize_authorship(
            {'ai_authorship_analysis': {'status': 'none'}}, False,
            provenance=[{'source': 'work.docx', 'checked': True, 'limitations': [], 'signals': [{
                'location': 'docProps/app.xml', 'basis': 'metadata', 'strength': 'medium',
                'observation': 'Застосунок документа згадує Gamma.'}]}])
        self.assertEqual(result['ai_authorship_state'], 'suspected')
        self.assertEqual(result['ai_generated_confidence'], 'medium')
        self.assertIn('Це ознаки, а не доведений факт', result['ai_generated_details'])
        self.assertEqual(result['ai_provenance_report']['signals'][0]['strength'], 'medium')

    def test_c2pa_signal_is_high_confidence(self):
        result = normalize_authorship(
            {'ai_authorship_analysis': {'status': 'none'}}, False,
            provenance=[{'source': 'photo.png', 'checked': True, 'limitations': [], 'signals': [{
                'location': 'C2PA маніфест', 'basis': 'c2pa', 'strength': 'strong',
                'observation': 'Валідний маніфест вказує на генеративне створення.'}]}])
        self.assertEqual(result['ai_authorship_state'], 'suspected')
        self.assertEqual(result['ai_generated_confidence'], 'high')

    def test_percent_below_tolerance_without_evidence_does_not_accuse(self):
        result = normalize_authorship({'ai_generated_percent': 10}, False)
        self.assertFalse(result['ai_generated_detected'])
        self.assertEqual(result['ai_authorship_state'], 'insufficient')


class OfficeMetadataTests(SimpleTestCase):
    def test_gamma_application_is_detected_but_marked_editable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'work.docx'
            with zipfile.ZipFile(path, 'w') as package:
                package.writestr('docProps/app.xml',
                                 '<Properties><Application>Gamma</Application></Properties>')
                package.writestr('docProps/core.xml',
                                 '<cp:coreProperties><dc:creator>Учень</dc:creator>'
                                 '<cp:lastModifiedBy>Інший</cp:lastModifiedBy></cp:coreProperties>')
                package.writestr('word/document.xml', '<w:document></w:document>')
            report = file_provenance(path)
            self.assertTrue(report['checked'])
            self.assertTrue(any('Gamma' in s['observation'] for s in report['signals']))
            self.assertTrue(any(s.get('strength') == 'medium' for s in report['signals']))
