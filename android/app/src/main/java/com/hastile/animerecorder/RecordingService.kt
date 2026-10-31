package com.hastile.animerecorder

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.SystemClock
import android.os.VibrationEffect
import android.os.Vibrator
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * 녹화 중 알림 리모컨. 폰으로 다른 앱을 쓰면서도
 * 알림/잠금화면에서 녹화 상태를 보고 일시정지, 정지할 수 있다.
 */
class RecordingService : Service() {
    companion object {
        private const val ACTION_WATCH = "watch"
        private const val ACTION_PAUSE = "pause"
        private const val ACTION_STOP = "stop"
        private const val EXTRA_HOST = "host"
        private const val CHANNEL = "recording"
        private const val NOTIF_ID = 1

        fun watch(context: Context, hostId: String) {
            val i = Intent(context, RecordingService::class.java).setAction(ACTION_WATCH).putExtra(EXTRA_HOST, hostId)
            runCatching { ContextCompat.startForegroundService(context, i) }
        }
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var hostId: String? = null
    private var base: String? = null
    private var pollJob: Job? = null
    private var status: JSONObject? = null
    private var flash: String? = null
    private var flashUntil = 0L
    private var stopArmedUntil = 0L
    private var idlePolls = 0
    private var failingSince = 0L

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        ensureChannel()
        // 시작 요청 후 5초 안에 반드시 포그라운드로 전환해야 한다
        ServiceCompat.startForeground(
            this, NOTIF_ID, buildNotification(),
            if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0,
        )
        intent?.getStringExtra(EXTRA_HOST)?.let { id ->
            if (id != hostId) {
                hostId = id
                base = null
            }
        }
        if (hostId == null) hostId = HostStore(this).lastHostId
        if (hostId == null) {
            finish()
            return START_NOT_STICKY
        }
        when (intent?.action) {
            ACTION_PAUSE -> act(if (isPaused()) "/api/record/resume" else "/api/record/pause",
                if (isPaused()) "다시 재생합니다" else "일시정지했습니다")
            ACTION_STOP -> {
                if (SystemClock.elapsedRealtime() < stopArmedUntil) {
                    stopArmedUntil = 0
                    act("/api/record/stop", "녹화를 저장했습니다")
                } else {
                    // 실수로 누르는 것 방지: 5초 안에 한 번 더 눌러야 정지
                    stopArmedUntil = SystemClock.elapsedRealtime() + 5000
                    showFlash("한 번 더 누르면 녹화를 멈춥니다", 5000)
                }
            }
        }
        startPolling()
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    // ---- 상태 확인 ----------------------------------------------------------
    private fun startPolling() {
        if (pollJob?.isActive == true) return
        pollJob = scope.launch {
            while (isActive) {
                poll()
                delay(2000)
            }
        }
    }

    private suspend fun currentBase(host: Host): String? {
        base?.let { return it }
        return Remote.resolve(this, host)?.base.also { base = it }
    }

    private suspend fun poll() {
        val host = HostStore(this).get(hostId) ?: return finish()
        val b = currentBase(host)
        if (b == null) return onFailure()
        try {
            val st = JSONObject(Api.request(b, "/api/status", token = host.token))
            status = st
            failingSince = 0
            val recording = st.optJSONObject("obs")?.optBoolean("recording") == true
            if (!recording && !st.optBoolean("starting")) {
                if (++idlePolls >= 3) return finish()
            } else {
                idlePolls = 0
            }
            update()
        } catch (e: ApiException) {
            if (e.code == 401) {
                showFlash("PC와의 연결 권한이 없습니다. 앱에서 다시 연결하세요", 10_000)
                return finish()
            }
            onFailure()
        } catch (e: Exception) {
            base = null
            Remote.invalidate(host.id)
            onFailure()
        }
    }

    private fun onFailure() {
        val now = SystemClock.elapsedRealtime()
        if (failingSince == 0L) failingSince = now
        status = null
        update()
        if (now - failingSince > 10 * 60_000) finish() // 10분 동안 연결이 안 되면 종료
    }

    private fun finish() {
        pollJob?.cancel()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    // ---- 버튼 동작 ----------------------------------------------------------
    private fun act(path: String, okMsg: String) {
        scope.launch {
            val host = HostStore(this@RecordingService).get(hostId) ?: return@launch
            val b = currentBase(host) ?: return@launch showFlash("PC에 연결할 수 없습니다")
            try {
                Api.post(b, path, host.token)
                showFlash(okMsg)
                vibrate()
                poll()
            } catch (e: ApiException) {
                showFlash(e.message ?: "실패했습니다")
            } catch (e: Exception) {
                base = null
                showFlash("PC에 연결할 수 없습니다")
            }
        }
    }

    private fun showFlash(msg: String, ms: Long = 3000) {
        flash = msg
        flashUntil = SystemClock.elapsedRealtime() + ms
        update()
        scope.launch {
            delay(ms + 100)
            update()
        }
    }

    private fun vibrate() {
        @Suppress("DEPRECATION")
        val v = getSystemService(Vibrator::class.java) ?: return
        runCatching { v.vibrate(VibrationEffect.createOneShot(40, VibrationEffect.DEFAULT_AMPLITUDE)) }
    }

    private fun isPaused(): Boolean = status?.optJSONObject("obs")?.optBoolean("paused") == true

    // ---- 알림 ---------------------------------------------------------------
    private fun ensureChannel() {
        val nm = getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, getString(R.string.channel_name), NotificationManager.IMPORTANCE_LOW).apply {
                    setShowBadge(false)
                    lockscreenVisibility = Notification.VISIBILITY_PUBLIC
                },
            )
        }
    }

    private fun update() {
        getSystemService(NotificationManager::class.java).notify(NOTIF_ID, buildNotification())
    }

    private fun pending(action: String, code: Int): PendingIntent {
        val i = Intent(this, RecordingService::class.java).setAction(action).putExtra(EXTRA_HOST, hostId)
        return PendingIntent.getForegroundService(this, code, i, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    private fun buildNotification(): Notification {
        val st = status
        val obs = st?.optJSONObject("obs")
        val recording = obs?.optBoolean("recording") == true
        val paused = obs?.optBoolean("paused") == true
        val durationMs = obs?.optLong("duration_ms") ?: 0L
        val session = st?.optJSONObject("session")
        val player = st?.optJSONObject("player")
        val hostName = HostStore(this).get(hostId)?.name ?: "PC"

        val title = when {
            st == null && failingSince > 0 -> "$hostName 연결 끊김 · 다시 시도 중"
            st == null -> "$hostName 연결 중…"
            paused -> "⏸ 일시정지 · ${fmtTime(durationMs / 1000)}"
            recording -> "● 녹화 중"
            else -> "녹화 대기"
        }
        val parts = mutableListOf<String>()
        session?.optString("title")?.takeIf { it.isNotBlank() }?.let { parts += it }
        if (player != null && player.optBoolean("found") && !player.isNull("duration")) {
            parts += "${fmtTime(player.optDouble("time").toLong())} / ${fmtTime(player.optDouble("duration").toLong())}"
        }
        val showFlash = flash != null && SystemClock.elapsedRealtime() < flashUntil
        val text = if (showFlash) flash!! else parts.joinToString(" · ").ifEmpty { "PC에서 녹화 중" }

        val open = PendingIntent.getActivity(
            this, 0,
            Intent(this, ControlActivity::class.java).putExtra(ControlActivity.EXTRA_HOST, hostId)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val b = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_rec)
            .setColor(0xFFFF4D5E.toInt())
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
        if (recording && !paused) {
            // 시스템이 알아서 흘러가게 표시하는 녹화 시간
            b.setUsesChronometer(true).setShowWhen(true).setWhen(System.currentTimeMillis() - durationMs)
        } else {
            b.setShowWhen(false)
        }
        if (recording) {
            val armed = SystemClock.elapsedRealtime() < stopArmedUntil
            b.addAction(0, if (paused) "▶ 다시 재생" else "⏸ 일시정지", pending(ACTION_PAUSE, 1))
            b.addAction(0, if (armed) "정지 확인" else "■ 정지하고 저장", pending(ACTION_STOP, 3))
        }
        return b.build()
    }
}
