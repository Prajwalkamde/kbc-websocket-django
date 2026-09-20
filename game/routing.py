from django.urls import re_path
from .consumers import GameConsumer

websocket_urlpatterns = [
    re_path(r"^ws/rooms/(?P<code>[A-Za-z0-9]{6})/$", GameConsumer.as_asgi()),
]
