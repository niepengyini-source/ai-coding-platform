from django.urls import path
from django.contrib.auth import views as auth
from django.views.generic import TemplateView
from coding import views as v
from coding import accounts as a
from coding import file_views as f
from coding import workflow_views as w
from coding.account_forms import PlatformLoginForm

urlpatterns = [
    path('login/', auth.LoginView.as_view(template_name='registration/login.html', authentication_form=PlatformLoginForm), name='login'),
    path('accounts/register/', a.register, name='register'),
    path('groups/join/', a.join_group, name='join_group'),
    path('groups/requests/<int:application_id>/cancel/', a.join_cancel, name='join_cancel'),
    path('logout/', auth.LogoutView.as_view(), name='logout'),
    path('accounts/password/', v.PlatformPasswordChangeView.as_view(), name='password_change'),
    path('accounts/password/done/', TemplateView.as_view(template_name='registration/password_change_done.html'), name='password_change_done'),
    path('', v.home, name='home'), path('guide/', v.guide, name='guide'),
    path('projects/new/', v.project_new, name='project_new'),
    path('p/<int:project_id>/', v.project_view, name='project'),
    path('p/<int:project_id>/members/', v.members, name='members'),
    path('p/<int:project_id>/invitation/', a.invitation_manage, name='invitation_manage'),
    path('p/<int:project_id>/requests/<int:application_id>/review/', a.join_review, name='join_review'),
    path('p/<int:project_id>/materials/', v.materials, name='materials'),
    path('p/<int:project_id>/units/', v.units, name='units'),
    path('p/<int:project_id>/units/<int:unit_id>/', v.change_unit, name='change_unit'),
    path('p/<int:project_id>/books/new/', v.book_new, name='book_new'),
    path('p/<int:project_id>/books/<int:book_id>/', v.codebook, name='codebook'),
    path('p/<int:project_id>/books/<int:book_id>/import/', f.book_import, name='book_import'),
    path('p/<int:project_id>/books/<int:book_id>/template/<str:file_format>/', f.book_template, name='book_template'),
    path('p/<int:project_id>/exports/', f.exports, name='exports'),
    path('p/<int:project_id>/books/<int:book_id>/freeze/', v.book_freeze, name='book_freeze'),
    path('p/<int:project_id>/codes/<int:code_id>/remove/', v.code_remove, name='code_remove'),
    path('p/<int:project_id>/rounds/new/', v.round_new, name='round_new'),
    path('p/<int:project_id>/quick-start/', w.quick_start, name='quick_start'),
    path('p/<int:project_id>/quick/<int:round_id>/finish/', w.quick_finish, name='quick_finish'),
    path('p/<int:project_id>/progress/', w.progress, name='progress'),
    path('api/projects/<int:project_id>/progress/', w.progress_api, name='project_progress'),
    path('p/<int:project_id>/history/', w.history, name='history'),
    path('p/<int:project_id>/history/<int:assignment_id>/', w.history_detail, name='history_detail'),
    path('p/<int:project_id>/history/<int:assignment_id>/reopen/', w.history_reopen, name='history_reopen'),
    path('p/<int:project_id>/archive/', v.archive, name='archive'),
    path('r/<int:round_id>/', v.workbench, name='workbench'),
    path('api/rounds/<int:round_id>/progress/', v.round_progress, name='round_progress'),
    path('r/<int:round_id>/manage/', v.round_manage, name='round_manage'),
    path('r/<int:round_id>/review/', v.review, name='review'),
    path('r/<int:round_id>/disagreements/', v.disagreement_queue, name='disagreements'),
    path('r/<int:round_id>/report/', v.reliability, name='reliability'),
    path('r/<int:round_id>/export/<str:mode>/', v.export_round, name='export_round'),
    path('api/assignments/<int:assignment_id>/save/', v.assignment_save, name='assignment_save'),
    path('assignments/<int:assignment_id>/reopen/', v.assignment_reopen, name='assignment_reopen'),
    path('api/rounds/<int:round_id>/units/<int:unit_id>/decision/', v.decision_save, name='decision_save'),
    path('r/<int:round_id>/units/<int:unit_id>/comments/', v.comment_add, name='comment_add'),
]
