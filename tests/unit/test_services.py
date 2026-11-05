from cardlegends.bot.handlers import profile_text


async def test_register_twice_preserves_identity_and_registration(service, memory_players):
    first = await service.register(2**40, "Первое имя", "old")
    player_id = memory_players.rows[2**40].id
    second = await service.register(2**40, "Новое имя", None)
    assert len(memory_players.rows) == 1
    assert memory_players.rows[2**40].id == player_id
    assert second.registered_at == first.registered_at
    assert second.display_name == "Новое имя"
    assert memory_players.rows[2**40].username is None


async def test_profile_expected_fields_and_html_escaping(service):
    registered = await service.register(42, '<b>Иван & "Анна"</b>', None)
    profile = await service.profile(42)
    assert profile == registered
    text = profile_text(profile)
    assert "42" in text
    assert "&lt;b&gt;Иван &amp; &quot;Анна&quot;&lt;/b&gt;" in text
    assert f"{registered.registered_at:%d.%m.%Y}" in text
    assert "Новичок" in text
    assert await service.profile(43) is None


async def test_display_name_respects_storage_limit(service):
    result = await service.register(42, "Я" * 300, None)
    assert len(result.display_name) == 255
