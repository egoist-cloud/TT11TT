"""
Тесты команд TT11TT (без реального подключения к Discord).

Запуск:
    python -m unittest test_commands -v

Проверяем:
  * !on / !off для egoist и egorik (включая русские алиасы);
  * права: неизвестный пользователь не может управлять аккаунтами,
    ID из ALLOWED_USER_IDS - может;
  * где принимаются команды: ЛС, любой канал при ALLOW_COMMANDS_IN_GUILD=true,
    белый список каналов, канал репортов;
  * один ответ на сообщение, даже если его видят все аккаунты;
  * 24/7-боты уступают дежурным и имеют обработчики для выхода из войса.
"""
import os
import sys
import unittest

# --- Окружение для бота (ДО импорта bot.py) --------------------------------
os.environ.setdefault("DISCORD_TOKEN_1", "fake-token-egoist")
os.environ.setdefault("DISCORD_TOKEN_2", "fake-token-egorik")
os.environ.setdefault("DISCORD_TOKEN_3", "fake-token-main")
os.environ.setdefault("DISCORD_TOKEN_4", "fake-token-second")
os.environ.setdefault("DISCORD_TOKEN_5", "fake-token-third")
os.environ.setdefault("VOICE_CHANNEL_ID", "111111111111111111")
os.environ.setdefault("GUILD_ID", "222222222222222222")
os.environ.setdefault("USER_ID_1", "333333333333333333")
os.environ.setdefault("USER_ID_2", "444444444444444444")
os.environ.pop("RENDER_URL", None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discord  # noqa: E402
import bot as botmod  # noqa: E402


# --- Моки ------------------------------------------------------------------
class FakeUser:
    def __init__(self, uid, name="user"):
        self.id = uid
        self.name = name

    def __str__(self):
        return self.name


class FakeChannel:
    def __init__(self, cid, name="chan"):
        self.id = cid
        self.name = name
        self.sent = []

    async def send(self, content):
        self.sent.append(content)
        return content


class FakeGuild:
    def __init__(self, gid):
        self.id = gid


class FakeMessage:
    _next_id = 1000

    def __init__(self, content, channel, author_id, guild=None, msg_id=None):
        FakeMessage._next_id += 1
        self.id = msg_id if msg_id is not None else FakeMessage._next_id
        self.content = content
        self.channel = channel
        self.author = FakeUser(author_id)
        self.guild = guild


class FakeClient:
    def __init__(self, uid, ready=True):
        self.user = FakeUser(uid)
        self._ready = ready

    def is_ready(self):
        return self._ready


class FakeWatcher:
    """Имитирует bot.Watcher (без сети)."""

    def __init__(self, key, name, token="t"):
        self.key = key
        self.name = name
        self.token = token
        self.client = FakeClient(555555555555555555)
        self.manual_mode = False
        self.disabled = True
        self.connect_calls = []
        self.disconnect_calls = 0
        self.connect_result = (True, "Успешно")

    async def connect(self, channel_id):
        self.connect_calls.append(channel_id)
        return self.connect_result

    async def disconnect(self):
        self.disconnect_calls += 1


class CommandTestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.channel = FakeChannel(999999999999999999)
        self.guild = FakeGuild(botmod.GUILD_ID)
        self.egoist = FakeWatcher("egoist", "Egoist")
        self.egorik = FakeWatcher("egorik", "Egorik")
        self.b247 = FakeClient(777777777777777777)
        self.allowed_id = min(botmod.ALLOWED_COMMAND_USERS)
        botmod.watcher_egoist = self.egoist
        botmod.watcher_egorik = self.egorik
        botmod._handled_msg_ids.clear()
        botmod._handled_msg_order.clear()
        botmod._warned_ignored.clear()
        self._all_guild = botmod.ALLOW_COMMANDS_IN_GUILD
        self._channel_ids = botmod.COMMAND_ALLOWED_CHANNEL_IDS
        self._commands_before = botmod.total_commands_handled

    def tearDown(self):
        botmod.ALLOW_COMMANDS_IN_GUILD = self._all_guild
        botmod.COMMAND_ALLOWED_CHANNEL_IDS = self._channel_ids

    def msg(self, content, channel=None, author_id=None, guild=None):
        if author_id is None:
            author_id = self.allowed_id
        return FakeMessage(content, channel or self.channel, author_id=author_id, guild=guild)


# --- Тесты -----------------------------------------------------------------
class TestOnOffCommands(CommandTestBase):
    async def test_on_egoist(self):
        await botmod._handle_message(self.egoist.client, self.msg("!on egoist"))
        self.assertTrue(self.egoist.manual_mode)
        self.assertFalse(self.egoist.disabled)
        self.assertEqual(self.egoist.connect_calls, [botmod.MAIN_CHANNEL_ID])
        self.assertIn("egoist", self.channel.sent[-1])

    async def test_off_egoist(self):
        await botmod._handle_message(self.egoist.client, self.msg("!off egoist"))
        self.assertFalse(self.egoist.manual_mode)
        self.assertTrue(self.egoist.disabled)
        self.assertEqual(self.egoist.disconnect_calls, 1)

    async def test_on_egorik(self):
        await botmod._handle_message(self.egorik.client, self.msg("!on egorik"))
        self.assertTrue(self.egorik.manual_mode)
        self.assertFalse(self.egorik.disabled)
        self.assertEqual(self.egorik.connect_calls, [botmod.MAIN_CHANNEL_ID])

    async def test_off_egorik(self):
        await botmod._handle_message(self.egorik.client, self.msg("!off egorik"))
        self.assertFalse(self.egorik.manual_mode)
        self.assertTrue(self.egorik.disabled)
        self.assertEqual(self.egorik.disconnect_calls, 1)

    async def test_russian_aliases(self):
        await botmod._handle_message(self.egoist.client, self.msg("!вкл egoist"))
        self.assertTrue(self.egoist.manual_mode)
        await botmod._handle_message(self.egoist.client, self.msg("!выкл egoist"))
        self.assertFalse(self.egoist.manual_mode)

    async def test_case_insensitive(self):
        await botmod._handle_message(self.egoist.client, self.msg("!ON EGOIST"))
        self.assertTrue(self.egoist.manual_mode)

    async def test_wrong_target_rejected(self):
        await botmod._handle_message(self.egoist.client, self.msg("!on vasya"))
        self.assertFalse(self.egoist.manual_mode)
        self.assertEqual(self.egoist.connect_calls, [])

    async def test_missing_token_reports_error(self):
        botmod.watcher_egoist = None
        await botmod._handle_message(self.egorik.client, self.msg("!on egoist"))
        self.assertIn("не настроен", self.channel.sent[-1])

    async def test_failed_connect_rolls_back(self):
        self.egoist.connect_result = (False, "Канал не найден")
        await botmod._handle_message(self.egoist.client, self.msg("!on egoist"))
        self.assertFalse(self.egoist.manual_mode)
        self.assertTrue(self.egoist.disabled)
        self.assertIn("Ошибка", self.channel.sent[-1])

    async def test_all_on_off(self):
        await botmod._handle_message(self.egoist.client, self.msg("!all on"))
        self.assertTrue(self.egoist.manual_mode)
        self.assertTrue(self.egorik.manual_mode)
        await botmod._handle_message(self.egoist.client, self.msg("!all off"))
        self.assertFalse(self.egoist.manual_mode)
        self.assertFalse(self.egorik.manual_mode)

    async def test_reserve_without_token(self):
        await botmod._handle_message(self.egoist.client, self.msg("!reserve on"))
        self.assertIn("RESERVE не настроен", self.channel.sent[-1])


class TestAccessControl(CommandTestBase):
    async def test_unknown_user_ignored(self):
        await botmod._handle_message(self.egoist.client, self.msg("!on egoist", author_id=424242))
        self.assertFalse(self.egoist.manual_mode)
        self.assertEqual(self.channel.sent, [])

    async def test_allowed_user_from_env(self):
        old = set(botmod.ALLOWED_COMMAND_USERS)
        botmod.ALLOWED_COMMAND_USERS = old | {123}
        try:
            await botmod._handle_message(self.egoist.client, self.msg("!on egoist", author_id=123))
            self.assertTrue(self.egoist.manual_mode)
        finally:
            botmod.ALLOWED_COMMAND_USERS = old

    async def test_any_guild_channel_by_default(self):
        botmod.ALLOW_COMMANDS_IN_GUILD = True
        ch = FakeChannel(555555555555555555)
        await botmod._handle_message(self.egoist.client, self.msg("!on egoist", channel=ch, guild=self.guild))
        self.assertTrue(self.egoist.manual_mode)

    async def test_guild_channel_blocked_when_disabled(self):
        botmod.ALLOW_COMMANDS_IN_GUILD = False
        botmod.COMMAND_ALLOWED_CHANNEL_IDS = {botmod.REPORT_CHANNEL_ID}
        ch = FakeChannel(555555555555555555)
        await botmod._handle_message(self.egoist.client, self.msg("!on egoist", channel=ch, guild=self.guild))
        self.assertFalse(self.egoist.manual_mode)

    async def test_whitelisted_channel(self):
        botmod.ALLOW_COMMANDS_IN_GUILD = False
        ch = FakeChannel(987654321098765432)
        botmod.COMMAND_ALLOWED_CHANNEL_IDS = {ch.id}
        await botmod._handle_message(self.egoist.client, self.msg("!on egoist", channel=ch, guild=self.guild))
        self.assertTrue(self.egoist.manual_mode)

    async def test_report_channel_always_allowed(self):
        botmod.ALLOW_COMMANDS_IN_GUILD = False
        ch = FakeChannel(botmod.REPORT_CHANNEL_ID)
        await botmod._handle_message(self.egoist.client, self.msg("!on egoist", channel=ch, guild=self.guild))
        self.assertTrue(self.egoist.manual_mode)

    async def test_message_in_dm_allowed(self):
        botmod.ALLOW_COMMANDS_IN_GUILD = False
        dm = FakeChannel(0)
        await botmod._handle_message(self.egoist.client, self.msg("!on egoist", channel=dm, guild=None))
        self.assertTrue(self.egoist.manual_mode)

    async def test_own_message_ignored(self):
        await botmod._handle_message(self.egoist.client,
                                     self.msg("!on egoist", author_id=self.egoist.client.user.id))
        self.assertFalse(self.egoist.manual_mode)


class TestDispatchBehaviour(CommandTestBase):
    async def test_single_response_for_all_accounts(self):
        message = self.msg("!on egoist")
        await botmod._handle_message(self.egoist.client, message)
        await botmod._handle_message(self.egorik.client, message)
        await botmod._handle_message(self.b247, message)
        self.assertEqual(len(self.channel.sent), 1)

    async def test_24_7_bot_defers_to_watcher(self):
        await botmod._handle_message(self.b247, self.msg("!on egoist"))
        self.assertEqual(self.channel.sent, [])
        self.assertFalse(self.egoist.manual_mode)

    async def test_plain_message_ignored(self):
        await botmod._handle_message(self.egoist.client, self.msg("привет как дела"))
        self.assertEqual(self.channel.sent, [])
        self.assertEqual(botmod.total_commands_handled, self._commands_before)

    async def test_other_commands_respond(self):
        for cmd in ("!panel", "!ping", "!help"):
            self.channel.sent.clear()
            await botmod._handle_message(self.egoist.client, self.msg(cmd))
            self.assertTrue(self.channel.sent, f"Команда {cmd} не дала ответа")


class Test247Events(unittest.TestCase):
    def test_handlers_registered(self):
        client = discord.Client(chunk_guilds_at_startup=False)
        botmod._attach_247_events("main", client)
        for handler in ("on_ready", "on_resumed", "on_voice_state_update"):
            self.assertTrue(hasattr(client, handler), f"нет обработчика {handler}")

    def test_voice_supported_helper_exists(self):
        self.assertIsInstance(botmod._voice_supported(), bool)


if __name__ == "__main__":
    unittest.main(verbosity=2)
