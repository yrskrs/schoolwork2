import json
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand

from feed.models import Submission


class Command(BaseCommand):
    help = 'Порахувати precision/recall виявлення ШІ за мітками вчителя (P3).'

    def add_arguments(self, parser):
        parser.add_argument('--json-out', default='', help='Записати метрики у JSON-файл')

    def handle(self, *args, **options):
        rows = list(Submission.objects.filter(
            ai_authorship_teacher_label__in=('ai', 'not_ai')).only(
            'id', 'ai_authorship_state', 'ai_authorship_teacher_label', 'file'))
        tp = fp = fn = tn = 0
        by_type = defaultdict(lambda: Counter())
        for sub in rows:
            predicted = sub.ai_authorship_state == 'suspected'
            actual = sub.ai_authorship_teacher_label == 'ai'
            if predicted and actual:
                tp += 1
            elif predicted and not actual:
                fp += 1
            elif actual:
                fn += 1
            else:
                tn += 1
            suffix = (sub.file.name.rsplit('.', 1)[-1].lower() if sub.file and '.' in sub.file.name else 'none')
            by_type[suffix]['tp' if predicted and actual else
                        'fp' if predicted else
                        'fn' if actual else 'tn'] += 1
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        fpr = fp / (fp + tn) if (fp + tn) else None
        payload = {
            'labeled': len(rows), 'positive_labels': tp + fn, 'predicted_positive': tp + fp,
            'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
            'precision': round(precision, 4) if precision is not None else None,
            'recall': round(recall, 4) if recall is not None else None,
            'fpr': round(fpr, 4) if fpr is not None else None,
            'by_file_type': {key: dict(value) for key, value in sorted(by_type.items())},
        }
        text = json.dumps(payload, ensure_ascii=False, indent=1)
        self.stdout.write('CALIBRATION ' + text)
        if options['json_out']:
            with open(options['json_out'], 'w', encoding='utf-8') as fh:
                fh.write(text)
            self.stdout.write(f'JSON: {options["json_out"]}')
