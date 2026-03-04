"""
find.py - Sopel Spelling Correction Plugin
This plugin will fix spelling errors if someone corrects them
using the sed notation (s///) commonly found in vi/vim.

Copyright 2011, Michael Yanovich, yanovich.net
Copyright 2013, Elsie Powell, embolalia.com
Copyright 2020, dgw, technobabbl.es
Includes contributions from: Matt Meinwald, and Morgan Goose
Licensed under the Eiffel Forum License 2.

https://sopel.chat
"""
from __future__ import annotations

from collections import deque
import re

from sopel import plugin
from sopel.formatting import bold
from sopel.privileges import AccessLevel


# Map channel mode letters to privilege levels required to see
# messages sent with the corresponding STATUSMSG prefix
_MODE_PRIVILEGES = {
    "v": AccessLevel.VOICE,
    "h": AccessLevel.HALFOP,
    "o": AccessLevel.OP,
    "a": AccessLevel.ADMIN,
    "q": AccessLevel.OWNER,
    "y": AccessLevel.OPER,
    "Y": AccessLevel.OPER,
}


def _get_privilege_for_prefix(bot, prefix):
    """Get the minimum privilege level to see a STATUSMSG with ``prefix``.

    :param bot: the running Sopel instance
    :param prefix: the STATUSMSG prefix character (e.g. ``@`` or ``+``)
    :return: the required :class:`~sopel.privileges.AccessLevel`, or ``None``
             if the prefix can't be mapped to a known privilege
    """
    try:
        prefix_map = bot.isupport.PREFIX  # dict of mode -> prefix_char
    except AttributeError:
        return None

    for mode, pfx in prefix_map.items():
        if pfx == prefix:
            return _MODE_PRIVILEGES.get(mode)

    return None


def setup(bot):
    if 'find_lines' not in bot.memory:
        bot.memory['find_lines'] = bot.make_identifier_memory()


def shutdown(bot):
    try:
        del bot.memory['find_lines']
    except KeyError:
        pass


@plugin.echo
@plugin.rule('.*')
@plugin.priority('low')
@plugin.require_chanmsg
@plugin.unblockable
def collectlines(bot, trigger):
    """Create a temporary log of what people say"""
    line = trigger.group()
    if line.startswith('s/') or line.startswith('s|'):
        # Don't remember substitutions
        return

    # Add a log for the channel and nick, if there isn't already one
    if trigger.sender not in bot.memory['find_lines']:
        bot.memory['find_lines'][trigger.sender] = bot.make_identifier_memory()
    if trigger.nick not in bot.memory['find_lines'][trigger.sender]:
        bot.memory['find_lines'][trigger.sender][trigger.nick] = deque(maxlen=10)

    # Update in-memory list of the user's lines in the channel
    line_list = bot.memory['find_lines'][trigger.sender][trigger.nick]

    # Messages are stored as (text, status_prefix) tuples,
    # in reverse order (most recent first)
    if line.startswith('\x01ACTION'):
        line_list.appendleft((line[:-1], trigger.status_prefix))
    else:
        line_list.appendleft((line, trigger.status_prefix))


def _cleanup_channel(bot, channel):
    bot.memory['find_lines'].pop(channel, None)


def _cleanup_nickname(bot, nick, channel=None):
    if channel:
        bot.memory['find_lines'].get(channel, {}).pop(nick, None)
    else:
        for channel in bot.memory['find_lines'].keys():
            bot.memory['find_lines'][channel].pop(nick, None)


@plugin.echo
@plugin.event('PART')
@plugin.priority('low')
@plugin.unblockable
def part_cleanup(bot, trigger):
    """Clean up cached data when a user leaves a channel."""
    if trigger.nick == bot.nick:
        # Nuke the whole channel cache, boys, we're outta here!
        _cleanup_channel(bot, trigger.sender)
    else:
        # Someone else left; clean up after them
        _cleanup_nickname(bot, trigger.nick, trigger.sender)


@plugin.echo
@plugin.event('QUIT')
@plugin.priority('low')
@plugin.unblockable
def quit_cleanup(bot, trigger):
    """Clean up cached data after a user quits IRC."""
    # If Sopel itself quits, shutdown() will handle the cleanup.
    _cleanup_nickname(bot, trigger.nick)


@plugin.echo
@plugin.event('KICK')
@plugin.priority('low')
@plugin.unblockable
def kick_cleanup(bot, trigger):
    """Clean up cached data when a user is kicked from a channel."""
    nick = bot.make_identifier(trigger.args[1])
    if nick == bot.nick:
        # We got kicked! Nuke the whole channel.
        _cleanup_channel(bot, trigger.sender)
    else:
        # Clean up after the poor sod (or more likely, spammer) who got the boot
        _cleanup_nickname(bot, nick, trigger.sender)


# Match nick, s/find/replace/flags. Flags and nick are optional, nick can be
# followed by comma or colon, anything after the first space after the third
# slash is ignored, and you can use either a slash or a pipe.
# If you want to search for an actual slash AND a pipe in the same message,
# you can escape your separator, in old and/or new.
@plugin.rule(r"""(?:
             (?P<nick>\S+)     # Catch a nick in group 1
             [:,]\s+)?         # Followed by optional colon/comma and whitespace
             s(?P<sep>/)       # The literal s and a separator / as group 2
             (?P<old>          # Group 3 is the thing to find
               (?:\\\\|\\/|[^/])+   # One or more non-slashes or escaped slashes
             )
             /                 # The separator again
             (?P<new>          # Group 4 is what to replace with
               (?:\\\\|\\/|[^/])*   # One or more non-slashes or escaped slashes
             )
             (?:/              # Optional separator followed by group 5 (flags)
                (?P<flags>\S+)
             )?
            """)
@plugin.rule(r"""(?:
             (?P<nick>\S+)     # Catch a nick in group 1
             [:,]\s+)?         # Followed by optional colon/comma and whitespace
             s(?P<sep>\|)      # The literal s and a separator | as group 2
             (?P<old>          # Group 3 is the thing to find
               (?:\\\\|\\\||[^|])+  # One or more non-pipe or escaped pipe
             )
             \|                # The separator again
             (?P<new>          # Group 4 is what to replace with
               (?:\\\\|\\\||[^|])*  # One or more non-pipe or escaped pipe
             )
             (?:\|             # Optional separator followed by group 5 (flags)
                (?P<flags>\S+)
             )?
            """)
@plugin.priority('high')
def findandreplace(bot, trigger):
    # Don't bother in PM
    if trigger.is_privmsg:
        return

    # Correcting other person vs self.
    rnick = bot.make_identifier(trigger.group('nick') or trigger.nick)

    # only do something if there is conversation to work with
    history = bot.memory['find_lines'].get(trigger.sender, {}).get(rnick, None)
    if not history:
        return

    sep = trigger.group('sep')
    escape_sequence_pattern = re.compile(r'\\[\\%s]' % sep)

    old = escape_sequence_pattern.sub(decode_escape, trigger.group('old'))
    new = trigger.group('new')
    me = False  # /me command
    flags = trigger.group('flags') or ''

    # only clean/format the new string if it's non-empty
    if new:
        new = escape_sequence_pattern.sub(decode_escape, new)

    # If g flag is given, replace all. Otherwise, replace once.
    if 'g' in flags:
        count = -1
    else:
        count = 1

    # repl is a dynamically defined function which performs the substitution.
    # i flag turns off case sensitivity. re.U turns on unicode replacement.
    if 'i' in flags:
        regex = re.compile(re.escape(old), re.U | re.I)

        def repl(line, subst):
            return re.sub(regex, subst, line, count == 1)
    else:
        def repl(line, subst):
            return line.replace(old, subst, count)

    # Look back through the user's lines in the channel until you find a line
    # where the replacement works
    new_line = new_display = None
    matched_status_prefix = None
    for line, status_prefix in history:
        # Skip messages the user doesn't have privilege to see
        if status_prefix:
            required_level = _get_privilege_for_prefix(bot, status_prefix)
            channel_obj = bot.channels.get(trigger.sender)
            if required_level is None or channel_obj is None:
                # Can't verify privileges; skip to be safe
                continue
            if not channel_obj.has_privilege(trigger.nick, required_level):
                continue

        if line.startswith("\x01ACTION"):
            me = True  # /me command
            line = line[8:]
        else:
            me = False
        replaced = repl(line, new)
        if replaced != line:  # we are done
            new_line = replaced
            new_display = repl(line, bold(new))
            matched_status_prefix = status_prefix
            break

    if not new_line:
        return  # Didn't find anything

    # Save the new "edited" message, preserving original status prefix.
    action = (me and '\x01ACTION ') or ''  # If /me message, prepend \x01ACTION
    # history is in most-recent-first order
    history.appendleft((action + new_line, matched_status_prefix))

    # output
    if not me:
        new_display = 'meant to say: %s' % new_display
    if trigger.group(1):
        msg = '%s thinks %s %s' % (trigger.nick, rnick, new_display)
    else:
        msg = '%s %s' % (trigger.nick, new_display)

    # If the matched message was sent with a STATUSMSG prefix,
    # address the corrected output to the same restricted audience.
    if matched_status_prefix:
        bot.say(msg, matched_status_prefix + str(trigger.sender))
    else:
        bot.say(msg)


def decode_escape(match):
    print("Substituting %s" % match.group(0))
    return {
        r'\\': '\\',
        r'\|': '|',
        r'\/': '/',
    }[match.group(0)]
