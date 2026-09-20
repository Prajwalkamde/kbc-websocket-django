from django.urls import path
from django.contrib.auth import views as auth_views
from . import api

urlpatterns = [
    path("", api.home, name="home"),
    path("host/", api.host_page, name="host"),
    path("host/<str:code>/", api.host_page, name="host-room"),
    path("join/", api.join_page, name="join"),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("play/<str:code>/", api.player_page, name="player"),
    path("api/rooms/create/", api.api_create_room, name="api-create-room"),
    path("api/rooms/join/", api.api_join_room, name="api-join-room"),
    path("api/rooms/<str:code>/state/", api.api_state, name="api-state"),
    path("api/rooms/<str:code>/host-action/", api.api_host_action, name="api-host-action"),
    path("api/rooms/<str:code>/answer/", api.api_answer, name="api-answer"),
    path("api/rooms/<str:code>/lifeline/", api.api_lifeline, name="api-lifeline"),
    path("api/rooms/<str:code>/fastest-submit/", api.api_fastest_submit, name="api-fastest-submit"),
    path("api/rooms/<str:code>/audience-vote/", api.api_audience_vote, name="api-audience-vote"),
    path("api/rooms/<str:code>/expert-answer/", api.api_expert_answer, name="api-expert-answer"),
    path("api/rooms/<str:code>/poll/<int:poll_id>/", api.api_poll, name="api-poll"),
]
