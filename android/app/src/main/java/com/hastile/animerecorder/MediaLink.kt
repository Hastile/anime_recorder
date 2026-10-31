package com.hastile.animerecorder

import android.content.ComponentName
import android.content.Context
import android.media.MediaMetadata
import android.media.session.MediaController
import android.media.session.MediaSessionManager
import android.media.session.PlaybackState
import android.os.SystemClock
import android.service.notification.NotificationListenerService
import androidx.core.app.NotificationManagerCompat

/**
 * 알림 접근 권한이 켜져 있어야 다른 앱(브라우저)의 미디어 세션을 읽을 수 있다.
 * 이 서비스는 그 권한을 받기 위한 자리일 뿐, 알림 내용은 읽지 않는다.
 */
class MediaWatcher : NotificationListenerService()

/** 폰 브라우저에서 재생 중인 영상의 위치/상태 (미디어 세션). */
class PhoneMedia(private val c: MediaController) {
    val packageName: String get() = c.packageName

    val playing: Boolean
        get() = c.playbackState?.state == PlaybackState.STATE_PLAYING

    /** 현재 재생 위치(초). 브라우저가 위치를 알려주지 않으면 null. */
    fun positionSec(): Double? {
        val st = c.playbackState ?: return null
        var pos = st.position
        if (pos < 0 || st.state == PlaybackState.STATE_NONE) return null
        if (st.state == PlaybackState.STATE_PLAYING && st.lastPositionUpdateTime > 0) {
            pos += ((SystemClock.elapsedRealtime() - st.lastPositionUpdateTime) * st.playbackSpeed).toLong()
        }
        return pos / 1000.0
    }

    val title: String?
        get() = c.metadata?.getString(MediaMetadata.METADATA_KEY_TITLE)?.takeIf { it.isNotBlank() }

    fun pause() = runCatching { c.transportControls.pause() }
    fun play() = runCatching { c.transportControls.play() }
}

object MediaLink {
    fun isEnabled(context: Context): Boolean =
        NotificationManagerCompat.getEnabledListenerPackages(context).contains(context.packageName)

    /** 해당 앱(브라우저)의 미디어 세션. 재생 중인 것을 우선. */
    fun find(context: Context, pkg: String?): PhoneMedia? {
        if (pkg == null || !isEnabled(context)) return null
        return try {
            val msm = context.getSystemService(MediaSessionManager::class.java) ?: return null
            val sessions = msm.getActiveSessions(ComponentName(context, MediaWatcher::class.java))
                .filter { it.packageName == pkg }
            val best = sessions.firstOrNull { it.playbackState?.state == PlaybackState.STATE_PLAYING }
                ?: sessions.firstOrNull()
            best?.let { PhoneMedia(it) }
        } catch (e: SecurityException) {
            null
        }
    }
}
