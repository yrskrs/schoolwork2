"""Large lists retain scoped access, global search, bounded HTML and lazy QR."""
from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from lxml import html
from .models import Teacher, AccessLink, TailscaleConnection, ClassGroup, Student


class AccessLinkListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user=User.objects.create_superuser('link-list-audit',password='synthetic')
        cls.teacher=Teacher.objects.create(user=cls.user,full_name='Вигаданий учитель')
        other=User.objects.create_user('other-link-owner')
        cls.other=Teacher.objects.create(user=other,full_name='Інший учитель')
        TailscaleConnection.objects.create(enabled=True,public_url='https://school.example.ts.net')
        for number in range(100):
            AccessLink.objects.create(teacher=cls.teacher,name=f'Link {number:03}',revoked_at=timezone.now() if number%2 else None)
        AccessLink.objects.create(teacher=cls.other,name='Foreign secret link')

    def setUp(self):
        self.client.force_login(self.user)

    def page(self,**params):
        response=self.client.get(reverse('access_links'),params)
        self.assertEqual(response.status_code,200)
        return response

    def test_hundred_links_have_bounded_page_and_no_foreign_rows(self):
        response=self.page()
        self.assertEqual(response.context['total_count'],100)
        self.assertEqual(len(response.context['links']),15)
        self.assertEqual(response.context['page_obj'].paginator.num_pages,7)
        self.assertNotContains(response,'Foreign secret link')
        tree=html.fromstring(response.content)
        self.assertEqual(len(tree.xpath('//details[contains(@class,"access-link-card")]')),15)
        self.assertTrue(all(not image.get('src') and image.get('data-src') for image in tree.xpath('//img[@data-src]')))
        self.assertIn('no-store',response.headers['Cache-Control'])

    def test_search_finds_old_link_beyond_first_page_and_empty_results_keep_form(self):
        response=self.page(q='Link 000')
        self.assertEqual([link.name for link in response.context['links']],['Link 000'])
        tree=html.fromstring(response.content)
        self.assertEqual(tree.xpath('//*[@id="access-search-input"]/@value'), ['Link 000'])
        self.assertEqual(tree.xpath('//*[@id="navbar-search"]/@value'), [''])
        response=self.page(q='not-existing')
        self.assertEqual(len(response.context['links']),0)
        self.assertContains(response,'id="access-filter-form"')
        self.assertContains(response,'Нічого не знайдено')

    def test_status_filters_counts_and_page_size_are_consistent(self):
        for status,expected in [('active',True),('inactive',False)]:
            response=self.page(status=status,per_page='30')
            self.assertEqual(response.context['active_count'],50)
            self.assertEqual(response.context['inactive_count'],50)
            self.assertEqual(response.context['page_obj'].paginator.count,50)
            self.assertEqual(len(response.context['links']),30)
            self.assertTrue(all(link.active==expected for link in response.context['links']))
        self.assertEqual(len(self.page(per_page='1000000').context['links']),15)
        self.assertEqual(len(self.page(page='not-a-page').context['links']),15)

    def test_expired_and_disabled_targets_are_inactive_and_name_search_crosses_fields(self):
        group=ClassGroup.objects.create(name='7-А',grade=7,letter='А',integration_active=False)
        student=Student.objects.create(class_group=group,first_name='Олена',last_name='Перевірка')
        created=[AccessLink.objects.create(teacher=self.teacher,name='Expired',expires_at=timezone.now()-timedelta(seconds=1)),
                 AccessLink.objects.create(teacher=self.teacher,name='Class',kind='class',class_group=group),
                 AccessLink.objects.create(teacher=self.teacher,name='Pupil',kind='student',student=student)]
        response=self.page(status='inactive')
        self.assertTrue({link.pk for link in created}.issubset({link.pk for link in response.context['links']}))
        response=self.page(q='Перевірка Олена')
        self.assertEqual([link.pk for link in response.context['links']],[created[-1].pk])

    def test_invalid_save_keeps_form_open_in_table_mode(self):
        response=self.client.post(reverse('access_links'),{'action':'save','kind':'site','name':''})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.context['form'].errors)
        self.assertContains(response,'data-keep-open="true"')
