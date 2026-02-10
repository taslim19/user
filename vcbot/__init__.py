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
                await VIDEO_ON[chats].stop()
            VIDEO_ON.clear()
            await asyncio.sleep(3)
        if self._video:
            for chats in list(CLIENTS):
                if chats != self._chat:
                    await CLIENTS[chats].stop()
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
                path = "http://docs.evostream.com/sample_content/assets/sintel1m720p.mp4"
                if hasattr(self.group_call, "play"):
                    try:
                        from pytgcalls.types import MediaStream, AudioQuality, VideoQuality
                    except ImportError:
                        from pytgcalls import MediaStream, AudioQuality, VideoQuality
                    
                    stream_params = {"audio_parameters": AudioQuality.HIGH}
                    if self._video:
                        stream_params["video_parameters"] = VideoQuality.HD_720p

                    await self.group_call.play(
                        self._chat, 
                        MediaStream(path, **stream_params)
                    )
                else:
                    media_input = AudioPiped(path) if not self._video else VideoPiped(path)
                    await self.group_call.join_group_call(self._chat, media_input)
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


async def download(query, video=False):
    try:
        if query.startswith("https://") and "youtube" not in query.lower() and "youtu.be" not in query:
             return query, None, query, query, "Unknown"
        
        # Use yt-dlp for search and info extraction (more reliable than youtubesearchpython)
        import asyncio
        import json
        import shlex
        
        search_prefix = "" if query.startswith("http") else "ytsearch1:"
        # Use yt-dlp to get JSON info
        process = await asyncio.create_subprocess_shell(
            f"yt-dlp --print-json --no-playlist --flat-playlist {search_prefix}{shlex.quote(query)}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            LOGS.error(f"yt-dlp search failed: {stderr.decode()}")
            # Fallback to original method if yt-dlp fails (unlikely)
            pass 
        else:
            data = json.loads(stdout.decode().split('\n')[0])
            title = data.get('title', 'Unknown')
            link = data.get('webpage_url', data.get('url'))
            duration = data.get('duration_string') or str(data.get('duration', '0:00'))
            thumb = data.get('thumbnail')
            
            # Now get the stream link using the URL we found
            dl = await get_stream_link(link, video=video)
            return dl, thumb, title, link, duration

    except Exception as e:
        LOGS.error(f"Error in robust download: {e}")

    # Legacy/Fallback (original logic fixed)
    if query.startswith("https://") and "youtube" not in query.lower():
        thumb, duration = None, "Unknown"
        title = link = query
        dl = await get_stream_link(link, video=video)
    else:
        try:
            search = VideosSearch(query, limit=1).result()
            data = search["result"][0]
            link = data["link"]
            title = data["title"]
            duration = data.get("duration") or "♾"
            thumb = f"https://i.ytimg.com/vi/{data['id']}/hqdefault.jpg"
            dl = await get_stream_link(link, video=video)
        except Exception as e:
             LOGS.exception(f"Search failed: {e}")
             return None, None, "Not Found", query, "0:00"
              
    return dl, thumb, title, link, duration


async def get_stream_link(ytlink, video=False):
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    if video:
        stream = await bash(f'yt-dlp -g --user-agent "{ua}" -f "best[height<=?720][width<=?1280]" {ytlink}')
    else:
        # For music, force a single audio stream to ensure FFmpeg can handle it easily
        stream = await bash(f'yt-dlp -g --user-agent "{ua}" -f "ba[ext=m4a]/ba/b" {ytlink}')
    return stream[0].strip()


async def vid_download(query):
    search = VideosSearch(query, limit=1).result()
    data = search["result"][0]
    link = data["link"]
    video = await get_stream_link(link)
    title = data["title"]
    thumb = f"https://i.ytimg.com/vi/{data['id']}/hqdefault.jpg"
    duration = data.get("duration") or "♾"
    return video, thumb, title, link, duration


async def dl_playlist(chat, from_user, link):
    # untill issue get fix
    # https://github.com/alexmercerind/youtube-search-python/issues/107
    """
    vids = Playlist.getVideos(link)
    try:
        vid1 = vids["videos"][0]
        duration = vid1["duration"] or "♾"
        title = vid1["title"]
        song = await get_stream_link(vid1['link'])
        thumb = f"https://i.ytimg.com/vi/{vid1['id']}/hqdefault.jpg"
        return song[0], thumb, title, vid1["link"], duration
    finally:
        vids = vids["videos"][1:]
        for z in vids:
            duration = z["duration"] or "♾"
            title = z["title"]
            thumb = f"https://i.ytimg.com/vi/{z['id']}/hqdefault.jpg"
            add_to_queue(chat, None, title, z["link"], thumb, from_user, duration)
    """
    links = await get_videos_link(link)
    try:
        search = VideosSearch(links[0], limit=1).result()
        vid1 = search["result"][0]
        duration = vid1.get("duration") or "♾"
        title = vid1["title"]
        song = await get_stream_link(vid1["link"])
        thumb = f"https://i.ytimg.com/vi/{vid1['id']}/hqdefault.jpg"
        return song, thumb, title, vid1["link"], duration
    finally:
        for z in links[1:]:
            try:
                search = VideosSearch(z, limit=1).result()
                vid = search["result"][0]
                duration = vid.get("duration") or "♾"
                title = vid["title"]
                thumb = f"https://i.ytimg.com/vi/{vid['id']}/hqdefault.jpg"
                add_to_queue(chat, None, title, vid["link"], thumb, from_user, duration)
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
