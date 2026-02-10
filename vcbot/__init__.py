# Ultroid - UserBot
# Copyright (C) 2021-2022 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

# ----------------------------------------------------------#
#                                                           #
#    _   _ _   _____ ____   ___ ___ ____   __     ______    #
#   | | | | | |_   _|  _ \ / _ \_ _|  _ \  \ \   / / ___|   #
#   | | | | |   | | | |_) | | | | || | | |  \ \ / / |       #
#   | |_| | |___| | |  _ <| |_| | || |_| |   \ V /| |___    #
#    \___/|_____|_| |_| \_\\___/___|____/     \_/  \____|   #
#                                                           #
# ----------------------------------------------------------#


import asyncio
import os
import re
import traceback
from time import time
from traceback import format_exc

from pytgcalls import PyTgCalls
from pytgcalls import filters

# Compatibility layer for different py-tgcalls versions
try:
    from pytgcalls.types.input_stream import AudioPiped, VideoPiped
except ImportError:
    try:
        from pytgcalls.types import AudioPiped, VideoPiped
    except ImportError:
        try:
            from pytgcalls.types import AudioVideoPiped
            AudioPiped = VideoPiped = AudioVideoPiped
        except ImportError:
            # Fallback: create dummy classes for basic compatibility
            class AudioPiped:
                def __init__(self, path):
                    self.path = path
            
            class VideoPiped:
                def __init__(self, path):
                    self.path = path

try:
    from pytgcalls.types.stream import StreamDeleted
except ImportError:
    # Fallback for older versions
    class StreamDeleted:
        pass

# Monkey patch to fix PyTgCalls 3.x compat with custom Telethon wrappers (UltroidClient)
try:
    from pytgcalls.mtproto import mtproto_client
    from pytgcalls.mtproto.telethon_client import TelethonClient
    _orig_init = mtproto_client.MtProtoClient.__init__

    def _patched_init(self, cache_duration, client, *args, **kwargs):
        try:
            return _orig_init(self, cache_duration, client, *args, **kwargs)
        except Exception:
            # Force accept as Telethon client if validation fails (e.g. for UltroidClient)
            self._bind_client = TelethonClient(cache_duration, client)
            
    mtproto_client.MtProtoClient.__init__ = _patched_init
except Exception:
    pass
from telethon.errors.rpcerrorlist import (
    ParticipantJoinMissingError,
    ChatSendMediaForbiddenError,
)
from pyUltroid import HNDLR, LOGS, asst, udB, vcClient
from pyUltroid._misc._decorators import compile_pattern
from pyUltroid.fns.helper import (
    bash,
    downloader,
    inline_mention,
    mediainfo,
    time_formatter,
)
from pyUltroid.fns.admins import admin_check
from pyUltroid.fns.tools import is_url_ok
from pyUltroid.fns.ytdl import get_videos_link
from pyUltroid._misc import owner_and_sudos, sudoers
from pyUltroid._misc._assistant import in_pattern
from pyUltroid._misc._wrappers import eod, eor
from pyUltroid.version import __version__ as UltVer
from telethon import events
from telethon.tl import functions, types
from telethon.utils import get_display_name

try:
    from yt_dlp import YoutubeDL
except ImportError:
    YoutubeDL = None
    LOGS.error("'yt-dlp' not found!")

try:
   from youtubesearchpython import VideosSearch
except ImportError:
    VideosSearch = None

from strings import get_string

asstUserName = asst.me.username
LOG_CHANNEL = udB.get_key("LOG_CHANNEL")
ACTIVE_CALLS, VC_QUEUE = [], {}
MSGID_CACHE, VIDEO_ON = {}, {}
CLIENTS = {}


def VC_AUTHS():
    _vcsudos = udB.get_key("VC_SUDOS") or []
    return [int(a) for a in [*owner_and_sudos(), *_vcsudos]]


class Player:
    def __init__(self, chat, event=None, video=False):
        self._chat = int(chat)
        self._current_chat = event.chat_id if event else LOG_CHANNEL
        self._video = video
        if CLIENTS.get("GLOBAL"):
            self.group_call = CLIENTS["GLOBAL"]
        else:
            self.group_call = PyTgCalls(vcClient)
            
            # Universal handler for all updates (error catching)
            @self.group_call.on_update()
            async def _update_handler(client, update):
                LOGS.debug(f"PyTgCalls Update: {update}")
            
            # Check for ffmpeg
            import shutil
            if not shutil.which("ffmpeg"):
                LOGS.error("CRITICAL: 'ffmpeg' not found in PATH! Voice chat playback WILL NOT work.")
            
            CLIENTS["GLOBAL"] = self.group_call

        CLIENTS.update({self._chat: self.group_call})

    async def make_vc_active(self):
        # Start fallback client if necessary
        if getattr(self, '_pure_client_needs_start', False):
            if not self._pure_client.is_connected():
                await self._pure_client.connect()
            self._pure_client_needs_start = False
        try:
            await vcClient(
                functions.phone.CreateGroupCallRequest(
                    self._chat, title="🎧 Ultroid Music 🎶"
                )
            )
        except Exception as e:
            LOGS.exception(e)
            return False, e
        return True, None

    async def startCall(self):
        if VIDEO_ON:
            for chats in VIDEO_ON:
                try:
                    await VIDEO_ON[chats].stop()
                except Exception:
                    pass
            VIDEO_ON.clear()
            await asyncio.sleep(3)
        if self._video:
            for chats in list(CLIENTS):
                if chats != self._chat:
                    try:
                        await CLIENTS[chats].stop()
                    except Exception:
                        pass
                    del CLIENTS[chats]
            VIDEO_ON.update({self._chat: self.group_call})
        if self._chat not in ACTIVE_CALLS:
            try:
                # Register event handler compatible with installed PyTgCalls version
                if hasattr(self.group_call, "on_update"):
                    self.group_call.on_update()(self.playout_ended_handler)
                elif hasattr(self.group_call, "on_stream_end"):
                    self.group_call.on_stream_end()(self.playout_ended_handler)
                elif hasattr(self.group_call, "on_stream_deleted"):
                    self.group_call.on_stream_deleted()(self.playout_ended_handler)
                    
                # Ensure main client is connected
                if not vcClient.is_connected():
                    await vcClient.connect()

                try:
                    await self.group_call.start()
                except Exception as e:
                    if "already running" in str(e) or "PyTgCallsAlreadyRunning" in type(e).__name__:
                        pass
                    else:
                        raise e
            except Exception as e:
                LOGS.exception(e)
                return False, e
        return True, None

    async def on_network_changed(self, call, is_connected):
        chat = self._chat
        if is_connected:
            if chat not in ACTIVE_CALLS:
                ACTIVE_CALLS.append(chat)
        elif chat in ACTIVE_CALLS:
            ACTIVE_CALLS.remove(chat)

    async def playout_ended_handler(self, client, update):
        if isinstance(update, StreamDeleted):
            await self.play_from_queue()

    async def play_from_queue(self):
        chat_id = self._chat
        try:
            song, title, link, thumb, from_user, pos, dur, video = await get_from_queue(
                chat_id
            )
            if not song:
                LOGS.error(f"Failed to get stream for {title}. Skipping...")
                VC_QUEUE[chat_id].pop(pos)
                return await self.play_from_queue()
            # Update video state for the next song
            if video:
                VIDEO_ON.update({chat_id: self.group_call})
            elif chat_id in VIDEO_ON:
                try:
                    await self.group_call.stop_video()
                except Exception:
                    pass
                VIDEO_ON.pop(chat_id)

            try:
                if hasattr(self.group_call, 'play'):
                    try:
                        from pytgcalls.types import MediaStream, AudioQuality, VideoQuality
                    except ImportError:
                        from pytgcalls import MediaStream, AudioQuality, VideoQuality
                    
                    import asyncio
                    await asyncio.sleep(1)
                    LOGS.info(f"Playing in VC: {song}")
                    params = {"audio_parameters": AudioQuality.HIGH}
                    if video:
                        params["video_parameters"] = VideoQuality.HD_720p

                    await self.group_call.play(chat_id, MediaStream(song, **params))
                else:
                    LOGS.info(f"Playing in VC (legacy): {song}")
                    await self.group_call.change_stream(
                        chat_id,
                        AudioPiped(song) if not video else VideoPiped(song)
                    )
            except Exception as er:
                LOGS.exception(f"Playback error: {er}")
                await self.vc_joiner()
                if hasattr(self.group_call, 'play'):
                    try:
                        from pytgcalls.types import MediaStream, AudioQuality, VideoQuality
                    except ImportError:
                        from pytgcalls import MediaStream, AudioQuality, VideoQuality
                    
                    import asyncio
                    await asyncio.sleep(1)
                    LOGS.info(f"Retrying Play in VC: {song}")
                    params = {"audio_parameters": AudioQuality.HIGH}
                    if video:
                        params["video_parameters"] = VideoQuality.HD_720p

                    await self.group_call.play(chat_id, MediaStream(song, **params))
                else:
                    await self.group_call.change_stream(
                        chat_id,
                        AudioPiped(song) if not video else VideoPiped(song)
                    )
            if MSGID_CACHE.get(chat_id):
                await MSGID_CACHE[chat_id].delete()
                del MSGID_CACHE[chat_id]
            text = f"<strong>🎧 Now playing #{pos}: <a href={link}>{title}</a>\n⏰ Duration:</strong> <code>{dur}</code>\n👤 <strong>Requested by:</strong> {from_user}"

            try:
                xx = await vcClient.send_message(
                    self._current_chat,
                    f"<strong>🎧 Now playing #{pos}: <a href={link}>{title}</a>\n⏰ Duration:</strong> <code>{dur}</code>\n👤 <strong>Requested by:</strong> {from_user}",
                    file=thumb,
                    link_preview=False,
                    parse_mode="html",
                )

            except ChatSendMediaForbiddenError:
                xx = await vcClient.send_message(
                    self._current_chat, text, link_preview=False, parse_mode="html"
                )
            MSGID_CACHE.update({chat_id: xx})
            VC_QUEUE[chat_id].pop(pos)
            if not VC_QUEUE[chat_id]:
                VC_QUEUE.pop(chat_id)

        except (IndexError, KeyError):
            try:
                await self.group_call.leave_group_call(chat_id)
            except Exception:
                pass
            if self._chat in CLIENTS:
                del CLIENTS[self._chat]
            await vcClient.send_message(
                self._current_chat,
                f"• Successfully Left Vc : <code>{chat_id}</code> •",
                parse_mode="html",
            )
        except Exception as er:
            LOGS.exception(er)
            await vcClient.send_message(
                self._current_chat,
                f"<strong>ERROR:</strong> <code>{format_exc()}</code>",
                parse_mode="html",
            )

    async def vc_joiner(self):
        chat_id = self._chat
        done, err = await self.startCall()

        if done:
            await vcClient.send_message(
                self._current_chat,
                f"• Joined VC in <code>{chat_id}</code>",
                parse_mode="html",
            )

            return True
        await vcClient.send_message(
            self._current_chat,
            f"<strong>ERROR while Joining Vc -</strong> <code>{chat_id}</code> :\n<code>{err}</code>",
            parse_mode="html",
        )
        return False


# --------------------------------------------------


def vc_asst(dec, **kwargs):
    def ult(func):
        kwargs["func"] = (
            lambda e: not e.is_private and not e.via_bot_id and not e.fwd_from
        )
        handler = udB.get_key("VC_HNDLR") or HNDLR
        kwargs["pattern"] = compile_pattern(dec, handler)
        vc_auth = kwargs.get("vc_auth", True)
        key = udB.get_key("VC_AUTH_GROUPS") or {}
        if "vc_auth" in kwargs:
            del kwargs["vc_auth"]

        async def vc_handler(e):
            VCAUTH = list(key.keys())
            if not (
                (e.out)
                or (e.sender_id in VC_AUTHS())
                or (vc_auth and e.chat_id in VCAUTH)
            ):
                return
            elif vc_auth and key.get(e.chat_id):
                cha, adm = key.get(e.chat_id), key[e.chat_id]["admins"]
                if adm and not (await admin_check(e)):
                    return
            try:
                await func(e)
            except Exception:
                LOGS.exception("VC Error")
                await asst.send_message(
                    LOG_CHANNEL,
                    f"VC Error - <code>{UltVer}</code>\n\n<code>{e.text}</code>\n\n<code>{format_exc()}</code>",
                    parse_mode="html",
                )

        vcClient.add_event_handler(
            vc_handler,
            events.NewMessage(**kwargs),
        )

    return ult


# --------------------------------------------------


def add_to_queue(chat_id, song, song_name, link, thumb, from_user, duration, video=False):
    try:
        n = sorted(list(VC_QUEUE[chat_id].keys()))
        play_at = n[-1] + 1
    except BaseException:
        play_at = 1
    stuff = {
        play_at: {
            "song": song,
            "title": song_name,
            "link": link,
            "thumb": thumb,
            "from_user": from_user,
            "duration": duration,
            "video": video,
        }
    }
    if VC_QUEUE.get(chat_id):
        VC_QUEUE[int(chat_id)].update(stuff)
    else:
        VC_QUEUE.update({chat_id: stuff})
    return VC_QUEUE[chat_id]


def list_queue(chat):
    if VC_QUEUE.get(chat):
        txt, n = "", 0
        for x in list(VC_QUEUE[chat].keys())[:18]:
            n += 1
            data = VC_QUEUE[chat][x]
            txt += f'<strong>{n}. <a href={data["link"]}>{data["title"]}</a> :</strong> <i>By: {data["from_user"]}</i>\n'
        txt += "\n\n....."
        return txt


async def get_from_queue(chat_id):
    play_this = list(VC_QUEUE[int(chat_id)].keys())[0]
    info = VC_QUEUE[int(chat_id)][play_this]
    song = info.get("song")
    title = info["title"]
    link = info["link"]
    thumb = info["thumb"]
    from_user = info["from_user"]
    duration = info["duration"]
    video = info.get("video", False)
    if not song:
        song = await get_stream_link(link, video=video)
    return song, title, link, thumb, from_user, play_this, duration, video


# --------------------------------------------------


from .youtube import YouTube


async def download(query, video=False):
    if query.startswith("https://") and "youtube" not in query.lower() and "youtu.be" not in query:
        return query, None, query, query, "Unknown"

    try:
        # Search using Backend API (as requested)
        results = await YouTube.search(query, limit=1)
        if not results:
            return None, None, "Not Found", query, "0:00"
        
        data = results[0]
        # Normalize data from different sources (API vs Local)
        video_id = data.get("id") or data.get("video_id") or data.get("vidid")
        if not video_id and "link" in data:
            video_id = YouTube._extract_video_id(data["link"])
            
        title = data.get("title", "Unknown")
        duration = data.get("duration") or data.get("duration_string") or "♾"
        link = data.get("link") or f"https://www.youtube.com/watch?v={video_id}"
        thumb = data.get("thumbnail") or data.get("thumb") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        if isinstance(thumb, list):
             thumb = thumb[0].get("url") if thumb else None

        # Download locally (better stability)
        dl = await YouTube._download_video(video_id) if video else await YouTube._download_audio(video_id)
        
        return dl, thumb, title, link, duration

    except Exception as e:
        LOGS.error(f"Error in refactored download: {e}")
        return None, None, "Error", query, "0:00"


async def get_stream_link(ytlink, video=False):
    video_id = YouTube._extract_video_id(ytlink)
    if not video_id:
        return ytlink
    if video:
        return await YouTube._download_video(video_id)
    return await YouTube._download_audio(video_id)


async def vid_download(query):
    # Using the new generic download function
    return await download(query, video=True)


async def dl_playlist(chat, from_user, link):
    from .youtube import Playlist as YTPlaylist
    if not YTPlaylist:
         # Fallback to current method if Playlist is not available
         links = await get_videos_link(link)
    else:
        try:
            get_links = await YTPlaylist.get(link)
            links = [x['link'] for x in get_links.get('videos', [])]
        except Exception:
            links = await get_videos_link(link)

    if not links:
        return None, None, "Link Not Found", link, "0:00"

    try:
        # Get first song and play
        song, thumb, title, link, duration = await download(links[0])
        return song, thumb, title, link, duration
    finally:
        # Add rest to queue
        for z in links[1:]:
            try:
                # We don't download everything now, just add metadata to queue
                # Queue handler will download when it's time to play
                search = await YouTube.search(z, limit=1)
                if not search:
                    continue
                vid = search[0]
                video_id = vid.get("id") or vid.get("video_id") or vid.get("vidid")
                if not video_id and "link" in vid:
                    video_id = YouTube._extract_video_id(vid["link"])
                title = vid.get("title", "Unknown")
                duration = vid.get("duration") or vid.get("duration_string") or "♾"
                v_link = f"https://www.youtube.com/watch?v={video_id}"
                thumb = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                add_to_queue(chat, None, title, v_link, thumb, from_user, duration)
            except Exception as er:
                LOGS.exception(er)


async def file_download(event, reply, fast_download=True):
    thumb = "https://telegra.ph/file/22bb2349da20c7524e4db.mp4"
    title = reply.file.title or reply.file.name or f"{str(time())}.mp4"
    file = reply.file.name or f"{str(time())}.mp4"
    if fast_download:
        dl = await downloader(
            f"vcbot/downloads/{file}",
            reply.media.document,
            event,
            time(),
            f"Downloading {title}...",
        )

        dl = dl.name
    else:
        dl = await reply.download_media()
    duration = (
        time_formatter(reply.file.duration * 1000) if reply.file.duration else "🤷‍♂️"
    )
    if reply.document.thumbs:
        thumb = await reply.download_media("vcbot/downloads/", thumb=-1)
    return dl, thumb, title, reply.message_link, duration


# --------------------------------------------------
