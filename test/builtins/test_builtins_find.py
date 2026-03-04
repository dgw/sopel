"""Tests for Sopel's ``find`` plugin"""
from __future__ import annotations

import pytest

from sopel.formatting import bold
from sopel.privileges import AccessLevel
from sopel.tests import rawlist


TMP_CONFIG = """
[core]
owner = Admin
nick = Sopel
enable =
    find
host = irc.libera.chat
"""


@pytest.fixture
def bot(botfactory, configfactory):
    settings = configfactory('default.ini', TMP_CONFIG)
    return botfactory.preloaded(settings, ['find'])


@pytest.fixture
def bot_with_statusmsg(botfactory, configfactory):
    """Create a bot that knows about STATUSMSG and PREFIX."""
    settings = configfactory('default.ini', TMP_CONFIG)
    bot = botfactory.preloaded(settings, ['find'])
    bot._isupport = bot._isupport.apply(
        PREFIX=(('o', '@'), ('v', '+')),
        STATUSMSG=('@', '+'),
    )
    return bot


@pytest.fixture
def irc(bot, ircfactory):
    return ircfactory(bot)


@pytest.fixture
def user(userfactory):
    return userfactory('User')


@pytest.fixture
def other_user(userfactory):
    return userfactory('other_user')


@pytest.fixture
def channel():
    return '#testing'


REPLACES_THAT_WORK = (
    ("A simple line.", r"s/line/message/", f"A simple {bold('message')}."),
    ("An escaped / line.", r"s/\//slash/", f"An escaped {bold('slash')} line."),
    ("A piped line.", r"s|line|replacement|", f"A piped {bold('replacement')}."),
    ("An escaped | line.", r"s|\||pipe|", f"An escaped {bold('pipe')} line."),
    ("An escaped \\ line.", r"s/\\/backslash/", f"An escaped {bold('backslash')} line."),
    ("abABab", r"s/b/c/g", "abABab".replace('b', bold('c'))),  # g (global) flag
    ("ABabAB", r"s/b/c/i", f"A{bold('c')}abAB"),  # i (case-insensitive) flag
    ("ABabAB", r"s/b/c/ig", f"A{bold('c')}a{bold('c')}A{bold('c')}"),  # both flags
)


@pytest.mark.parametrize('original, command, result', REPLACES_THAT_WORK)
def test_valid_replacements(bot, irc, user, channel, original, command, result):
    """Verify that basic replacement functionality works."""
    irc.channel_joined(channel, [user.nick])

    irc.say(user, channel, original)
    irc.say(user, channel, command)

    assert len(bot.backend.message_sent) == 1, (
        "The bot should respond with exactly one line.")
    assert bot.backend.message_sent == rawlist(
        "PRIVMSG %s :%s meant to say: %s" % (channel, user.nick, result),
    )


def test_multiple_users(bot, irc, user, other_user, channel):
    """Verify that correcting another user's line works."""
    irc.channel_joined(channel, [user.nick, other_user.nick])

    irc.say(other_user, channel, 'Some weather we got yesterday')
    irc.say(user, channel, '%s: s/yester/to/' % other_user.nick)

    assert len(bot.backend.message_sent) == 1, (
        "The bot should respond with exactly one line.")
    assert bot.backend.message_sent == rawlist(
        "PRIVMSG %s :%s thinks %s meant to say: %s" % (
            channel, user.nick, other_user.nick,
            f"Some weather we got {bold('to')}day",
        ),
    )


def test_replace_the_replacement(bot, irc, user, channel):
    """Verify replacing text that was already replaced."""
    irc.channel_joined(channel, [user.nick])

    irc.say(user, channel, 'spam')
    irc.say(user, channel, 's/spam/eggs/')
    irc.say(user, channel, 's/eggs/bacon/')

    assert len(bot.backend.message_sent) == 2, (
        "The bot should respond twice.")
    assert bot.backend.message_sent == rawlist(
        "PRIVMSG %s :%s meant to say: %s" % (
            channel, user.nick, bold('eggs'),
        ),
        "PRIVMSG %s :%s meant to say: %s" % (
            channel, user.nick, bold('bacon'),
        ),
    )


def test_statusmsg_unprivileged_user_cannot_match(
    bot_with_statusmsg, ircfactory, userfactory, channel,
):
    """An unprivileged user must not be able to reveal STATUSMSG lines."""
    bot = bot_with_statusmsg
    irc = ircfactory(bot)
    # op_user is an op (@), regular_user has no special privileges
    op_user = userfactory('OpUser')
    regular_user = userfactory('RegUser')
    irc.channel_joined(channel, ['@' + op_user.nick, regular_user.nick])

    # OpUser sends a message to @#channel (ops only)
    irc.say(op_user, '@' + channel, 'secret ops message')

    # RegUser (not an op) tries to sed-correct it
    irc.say(regular_user, channel, '%s: s/secret/public/' % op_user.nick)

    assert len(bot.backend.message_sent) == 0, (
        "The bot must not respond when an unprivileged user tries to "
        "match a STATUSMSG line they can't see.")


def test_statusmsg_privileged_user_can_match(
    bot_with_statusmsg, ircfactory, userfactory, channel,
):
    """A user with sufficient privilege can correct STATUSMSG lines."""
    bot = bot_with_statusmsg
    irc = ircfactory(bot)
    # both users are ops
    op_user = userfactory('OpUser')
    other_op = userfactory('OtherOp')
    irc.channel_joined(channel, ['@' + op_user.nick, '@' + other_op.nick])

    # OpUser sends a message to @#channel (ops only)
    irc.say(op_user, '@' + channel, 'secret ops message')

    # OtherOp (also an op) corrects it
    irc.say(other_op, channel, '%s: s/secret/important/' % op_user.nick)

    assert len(bot.backend.message_sent) == 1, (
        "The bot should respond when a privileged user corrects "
        "a STATUSMSG line.")
    # The reply should be addressed to @#channel to preserve privacy
    assert bot.backend.message_sent == rawlist(
        "PRIVMSG @%s :%s thinks %s meant to say: %s" % (
            channel, other_op.nick, op_user.nick,
            f"{bold('important')} ops message",
        ),
    )


def test_statusmsg_skips_to_public_line(
    bot_with_statusmsg, ircfactory, userfactory, channel,
):
    """An unprivileged user's sed skips STATUSMSG lines, matches public ones."""
    bot = bot_with_statusmsg
    irc = ircfactory(bot)
    op_user = userfactory('OpUser')
    regular_user = userfactory('RegUser')
    irc.channel_joined(channel, ['@' + op_user.nick, regular_user.nick])

    # OpUser sends a public message first, then a STATUSMSG one
    irc.say(op_user, channel, 'hello world')
    irc.say(op_user, '@' + channel, 'secret world')

    # RegUser tries to correct "world" — should only match the public line
    irc.say(regular_user, channel, '%s: s/world/everyone/' % op_user.nick)

    assert len(bot.backend.message_sent) == 1
    assert bot.backend.message_sent == rawlist(
        "PRIVMSG %s :%s thinks %s meant to say: %s" % (
            channel, regular_user.nick, op_user.nick,
            f"hello {bold('everyone')}",
        ),
    )


def test_statusmsg_voiced_user_can_match_voice_prefix(
    bot_with_statusmsg, ircfactory, userfactory, channel,
):
    """A voiced user can correct lines sent with + STATUSMSG prefix."""
    bot = bot_with_statusmsg
    irc = ircfactory(bot)
    op_user = userfactory('OpUser')
    voiced_user = userfactory('VoicedUser')
    irc.channel_joined(channel, ['@' + op_user.nick, '+' + voiced_user.nick])

    # OpUser sends to +#channel (voiced users and above)
    irc.say(op_user, '+' + channel, 'voiced-only message')

    # VoicedUser corrects it
    irc.say(voiced_user, channel, '%s: s/voiced-only/special/' % op_user.nick)

    assert len(bot.backend.message_sent) == 1
    assert bot.backend.message_sent == rawlist(
        "PRIVMSG +%s :%s thinks %s meant to say: %s" % (
            channel, voiced_user.nick, op_user.nick,
            f"{bold('special')} message",
        ),
    )


def test_statusmsg_voiced_user_cannot_match_op_prefix(
    bot_with_statusmsg, ircfactory, userfactory, channel,
):
    """A voiced user cannot correct lines sent with @ STATUSMSG prefix."""
    bot = bot_with_statusmsg
    irc = ircfactory(bot)
    op_user = userfactory('OpUser')
    voiced_user = userfactory('VoicedUser')
    irc.channel_joined(channel, ['@' + op_user.nick, '+' + voiced_user.nick])

    # OpUser sends to @#channel (ops only)
    irc.say(op_user, '@' + channel, 'ops-only message')

    # VoicedUser (only +v, not +o) tries to correct it
    irc.say(voiced_user, channel, '%s: s/ops-only/public/' % op_user.nick)

    assert len(bot.backend.message_sent) == 0, (
        "A voiced user must not be able to match lines sent with @ prefix.")


def test_statusmsg_self_correct(
    bot_with_statusmsg, ircfactory, userfactory, channel,
):
    """A user can self-correct their own STATUSMSG line if privileged."""
    bot = bot_with_statusmsg
    irc = ircfactory(bot)
    op_user = userfactory('OpUser')
    irc.channel_joined(channel, ['@' + op_user.nick])

    # OpUser sends to @#channel then self-corrects (no target nick)
    irc.say(op_user, '@' + channel, 'osp message')
    irc.say(op_user, channel, 's/osp/ops/')

    assert len(bot.backend.message_sent) == 1
    assert bot.backend.message_sent == rawlist(
        "PRIVMSG @%s :%s meant to say: %s" % (
            channel, op_user.nick,
            f"{bold('ops')} message",
        ),
    )
