"""Student comments stay concise and grade-free, including historical records."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from .ai_student_feedback import AI_COMMENT_TAG, compact_student_feedback, public_ai_comment
from .models import AIErrorLog, SubmissionComment
from . import tests_ai_assessment_v4 as fixtures


class StudentFeedbackTextTests(SimpleTestCase):
    def test_grade_words_fractions_groups_and_json_are_excluded_but_task_numbers_remain(self):
        for grade in ['Оцінка: 8.', 'Результат — 10 балів.', 'ГР 1: 9.', 'Отримано 8/12.',
                      'Ваш результат 8 із 12.', 'Score: 10.', '**Оцінка:** 10', 'Отримано 8 б.', 'Оцінено на 10.']:
            text=compact_student_feedback(text=grade+'\n💡 Додай 2 приклади до вправи 6.')
            self.assertEqual(text,'💡 Додай 2 приклади до вправи 6.')
        self.assertNotIn('suggested_grade',compact_student_feedback(text='{"suggested_grade":10'))

    def test_only_main_summary_one_strength_and_two_actions_are_kept(self):
        result={'summary':'Зміст правильний.', 'strengths':['Є приклади.']*8,
                'weaknesses':['Додай джерела.']*8,
                'revision_advice':['Додай джерела.','Уточни висновок.','Перевір оформлення.'],
                'feedback_comment':'Додай джерела.', 'grade_explanation':'Оцінка 8 балів.'}
        text=compact_student_feedback(result)
        self.assertEqual(text.count('Додай джерела.'),1)
        self.assertEqual(len(text.splitlines()),4)
        self.assertNotIn('Перевір оформлення',text)
        self.assertNotIn('8',text)
        self.assertEqual(compact_student_feedback(text=text),text)

    def test_very_long_feedback_is_bounded_and_broken_json_is_hidden(self):
        text=compact_student_feedback({'summary':'Важливий висновок. '*100,
             'strengths':['Є приклади. '*100], 'revision_advice':['Додай джерела. '*100,'Уточни висновок. '*100]})
        self.assertLessEqual(len(text),700)
        self.assertEqual(compact_student_feedback(text='{"suggested_grade":8,"feedback_comment":"broken'), '')

    def test_teacher_grading_sections_removed_while_advice_survives(self):
        text='📌 **Висновок:**\nЗміст зрозумілий.\n🎯 **Чому така оцінка:**\n8 балів за зміст.\n📋 **Перевірка критеріїв:**\nПриватний критерій.\n🛠️ **Як покращити роботу:**\n1. Додай джерела.'
        public=public_ai_comment(AI_COMMENT_TAG+'\n'+text)
        self.assertIn('Додай джерела',public)
        for phrase in ['8','критерій','Чому така оцінка']:
            self.assertNotIn(phrase,public)

    def test_manual_teacher_comment_is_preserved(self):
        text='Оцінка 8. Зателефонуй учителю.'
        self.assertEqual(public_ai_comment(text),text)


class StudentFeedbackBoundaryTests(TestCase):
    setUp=fixtures.AssessmentV4Tests.setUp
    evaluate=fixtures.AssessmentV4Tests.evaluate
    result=fixtures.AssessmentV4Tests.result

    def test_teacher_assessment_retained_but_student_check_is_short_and_grade_free(self):
        from .views import _perform_student_ai_check
        result,_=self.evaluate(self.result(summary='Зміст правильний. Оцінка 8 балів.',
            feedback_comment='Оцінка 8. Додай приклади.',
            revision_advice=['Додай джерела.','Уточни висновок.','Перевір оформлення.']))
        with patch('feed.views.check_submission_duplicates',return_value={}):
            response=_perform_student_ai_check(self.sub,result)
        data=json.loads(response.content)
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.student_ai_checked)
        self.assertEqual(self.sub.student_ai_grade,result['suggested_grade'])
        self.assertNotIn('grade_explanation',data)
        self.assertNotIn('балів',data['feedback'])
        self.assertLessEqual(len(data['feedback']),700)
        self.assertEqual(self.sub.student_ai_feedback,data['feedback'])
        self.assertIn('Чому така оцінка',self.sub.ai_feedback)

    def test_historical_ai_comment_is_clean_on_read_without_changing_record(self):
        comment=SubmissionComment.objects.create(submission=self.sub,text='initial',author=self.user)
        original=AI_COMMENT_TAG+'\n🎯 **Чому така оцінка:**\n8 балів.\n🛠️ **Як покращити роботу:**\nДодай джерела.'
        SubmissionComment.objects.filter(pk=comment.pk).update(text=original)
        comment.refresh_from_db()
        self.assertNotIn('8',comment.get_public_text())
        self.assertIn('Додай джерела',comment.get_public_text())
        self.assertEqual(comment.text,original)

    def test_apply_ai_grade_saves_short_public_comment_and_preserves_teacher_result(self):
        self.sub.ai_suggested_grade='8'
        self.sub.ai_feedback='📌 **Висновок:**\nЗміст зрозумілий.\n🎯 **Чому така оцінка:**\n8 балів.\n🛠️ **Як покращити роботу:**\nДодай джерела.'
        self.sub.save()
        self.client.force_login(self.user)
        response=self.client.post(reverse('ai_apply_suggested_grade',args=[self.sub.pk]))
        self.assertEqual(response.status_code,200)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.grade,'8')
        self.assertIn('8 балів',self.sub.ai_feedback)
        comment=self.sub.comments.get()
        self.assertTrue(comment.text.startswith(AI_COMMENT_TAG))
        self.assertNotIn('8',comment.text)
        self.assertIn('Додай джерела',comment.text)

    def test_error_log_respects_selected_period(self):
        recent=AIErrorLog.objects.create(error_message='recent error')
        old=AIErrorLog.objects.create(error_message='old error')
        AIErrorLog.objects.filter(pk=old.pk).update(created_at=timezone.now()-timedelta(days=10))
        self.client.force_login(self.user)
        page=self.client.get(reverse('teacher_settings'),{'tab':'ai','ai_section':'errors','stats_days':'7'})
        self.assertEqual(page.context['total_ai_errors_count'],1)
        self.assertEqual([r.pk for r in page.context['ai_error_logs']],[recent.pk])
        page=self.client.get(reverse('teacher_settings'),{'tab':'ai','ai_section':'errors','stats_days':'all'})
        self.assertEqual(page.context['total_ai_errors_count'],2)
