package com.hastile.animerecorder

import android.accessibilityservice.AccessibilityService
import android.annotation.SuppressLint
import android.content.ComponentName
import android.content.Context
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.provider.Settings
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import kotlin.math.abs

/**
 * 폰 브라우저와 PC 녹화를 연동한다.
 *
 * - 브라우저를 보고 있을 때 화면 위에 ● 플로팅 버튼을 띄운다.
 * - 버튼을 누르면 지금 보고 있는 주소(주소창)와 재생 위치(미디어 세션)를 읽어
 *   PC가 같은 페이지를 같은 위치부터 녹화하게 한다.
 * - 녹화 중에는 폰에서 일시정지/재생/이동하면 PC도 따라간다.
 *
 * 주소창 내용은 버튼을 누를 때만 사용자의 PC로 보내며, 다른 곳으로 보내지 않는다.
 */
class LinkService : AccessibilityService() {
    companion object {
        val BROWSERS = setOf(
            "com.android.chrome", "com.chrome.beta", "com.chrome.dev", "com.chrome.canary",
            "com.sec.android.app.sbrowser", "com.sec.android.app.sbrowser.beta",
            "com.naver.whale", "com.microsoft.emmx", "org.mozilla.firefox", "org.mozilla.firefox_beta",
            "com.brave.browser", "com.opera.browser", "com.vivaldi.browser", "com.kiwibrowser.browser",
        )
        private val URL_BAR_IDS = listOf(
            "url_bar", "location_bar_edit_text", "mozac_browser_toolbar_url_view", "url_field", "address_bar_text",
        )

        fun isEnabled(context: Context): Boolean {
            val list = Settings.Secure.getString(context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES)
                ?: return false
            return list.split(':').any {
                val c = ComponentName.unflattenFromString(it)
                c?.packageName == context.packageName && c.className == LinkService::class.java.name
            }
        }
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private lateinit var wm: WindowManager
    private var bubble: TextView? = null
    private var panel: LinearLayout? = null
    private lateinit var bubbleLp: WindowManager.LayoutParams

    private var browserPkg: String? = null // 지금 화면에 있는 브라우저
    private var lastUrl: String? = null
    private var lastUrlAt = 0L

    // 녹화 상태 (PC에서 받아옴)
    private var recording = false
    private var busy = false
    private var pcPaused = false
    private var pcTime: Double? = null
    private var durationMs = 0L
    private var syncPkg: String? = null // 녹화를 시작한 브라우저 (재생 위치 동기화 대상)
    private var lastSyncAt = 0L
    private var userPaused = false
    private var pollJob: Job? = null

    // ---- 접근성 이벤트 ----------------------------------------------------
    override fun onServiceConnected() {
        wm = getSystemService(WindowManager::class.java)
        createBubble()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        val pkg = event.packageName?.toString() ?: return
        if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED && pkg != packageName) {
            when {
                pkg in BROWSERS -> browserPkg = pkg
                isTransient(pkg) -> Unit // 키보드, 알림창 등은 무시
                else -> browserPkg = null
            }
            updateVisibility()
        }
        if (pkg in BROWSERS) readUrl(pkg)
    }

    override fun onInterrupt() {}

    override fun onDestroy() {
        scope.cancel()
        runCatching { bubble?.let { wm.removeView(it) } }
        hidePanel()
        super.onDestroy()
    }

    private fun isTransient(pkg: String): Boolean =
        pkg.startsWith("com.android.systemui") || pkg.contains("inputmethod") || pkg.contains("keyboard") ||
            pkg.contains("honeyboard") || pkg == "android"

    /** 브라우저 주소창에서 지금 주소를 읽는다 (주소창이 보일 때만 가능 → 마지막 값을 기억). */
    private fun readUrl(pkg: String) {
        val now = System.currentTimeMillis()
        if (now - lastUrlAt < 400) return
        val root = rootInActiveWindow ?: return
        if (root.packageName?.toString() != pkg) return
        for (id in URL_BAR_IDS) {
            val nodes: List<AccessibilityNodeInfo> = root.findAccessibilityNodeInfosByViewId("$pkg:id/$id") ?: continue
            val node = nodes.firstOrNull() ?: continue
            if (node.isFocused) return // 주소를 입력하는 중
            val url = normalizeUrl(node.text?.toString()) ?: continue
            lastUrl = url
            lastUrlAt = now
            return
        }
    }

    private fun normalizeUrl(text: String?): String? {
        val t = text?.trim().orEmpty()
        if (t.isEmpty() || t.contains(' ') || !t.contains('.')) return null
        return if (t.startsWith("http://") || t.startsWith("https://")) t else "https://$t"
    }

    // ---- 플로팅 버튼 ------------------------------------------------------
    private fun dp(v: Int): Int =
        TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics).toInt()

    private fun round(color: Int, stroke: Int? = null, oval: Boolean = false) = GradientDrawable().apply {
        if (oval) shape = GradientDrawable.OVAL else cornerRadius = dp(16).toFloat()
        setColor(color)
        stroke?.let { setStroke(dp(2), it) }
    }

    @SuppressLint("ClickableViewAccessibility")
    private fun createBubble() {
        val prefs = getSharedPreferences("bubble", MODE_PRIVATE)
        val size = dp(60)
        val b = TextView(this).apply {
            gravity = Gravity.CENTER
            setTextColor(0xFFFFFFFF.toInt())
            setTypeface(typeface, Typeface.BOLD)
            textSize = 13f
            elevation = dp(6).toFloat()
            visibility = View.GONE
        }
        bubbleLp = WindowManager.LayoutParams(
            size, size,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = prefs.getInt("x", resources.displayMetrics.widthPixels - size - dp(12))
            y = prefs.getInt("y", resources.displayMetrics.heightPixels / 3)
        }
        val slop = ViewConfiguration.get(this).scaledTouchSlop
        var downX = 0f
        var downY = 0f
        var startX = 0
        var startY = 0
        var moved = false
        b.setOnTouchListener { _, e ->
            when (e.action) {
                MotionEvent.ACTION_DOWN -> {
                    downX = e.rawX; downY = e.rawY; startX = bubbleLp.x; startY = bubbleLp.y; moved = false
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = e.rawX - downX
                    val dy = e.rawY - downY
                    if (moved || abs(dx) > slop || abs(dy) > slop) {
                        moved = true
                        bubbleLp.x = startX + dx.toInt()
                        bubbleLp.y = startY + dy.toInt()
                        runCatching { wm.updateViewLayout(b, bubbleLp) }
                        hidePanel()
                    }
                }
                MotionEvent.ACTION_UP -> {
                    if (moved) {
                        prefs.edit().putInt("x", bubbleLp.x).putInt("y", bubbleLp.y).apply()
                    } else {
                        onBubbleClick()
                    }
                }
            }
            true
        }
        wm.addView(b, bubbleLp)
        bubble = b
        renderBubble()
    }

    private fun updateVisibility() {
        val show = (browserPkg != null || recording) && HostStore(this).lastHostId != null
        bubble?.visibility = if (show) View.VISIBLE else View.GONE
        if (!show) hidePanel()
    }

    private fun renderBubble() {
        val b = bubble ?: return
        when {
            busy -> {
                b.text = "…"
                b.background = round(0xE6262A33.toInt(), 0xFFFFC247.toInt(), oval = true)
            }
            recording -> {
                b.text = if (pcPaused) "❚❚" else fmtTime(durationMs / 1000)
                b.background = round(if (pcPaused) 0xE6806020.toInt() else 0xE6FF4D5E.toInt(), oval = true)
            }
            else -> {
                b.text = "● REC"
                b.background = round(0xE6262A33.toInt(), 0xFFFF4D5E.toInt(), oval = true)
            }
        }
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    private fun onBubbleClick() {
        if (busy) return
        if (recording) togglePanel() else startRecording()
    }

    // ---- 녹화 시작 --------------------------------------------------------
    private fun startRecording() {
        val store = HostStore(this)
        val host = store.get(store.lastHostId) ?: return toast("앱에서 PC를 먼저 연결하세요")
        val pkg = browserPkg
        pkg?.let { readUrl(it) }
        val url = lastUrl ?: return toast("주소를 읽지 못했어요. 화면을 살짝 내려 주소창이 보이게 한 뒤 다시 눌러주세요")
        val media = MediaLink.find(this, pkg)
        val pos = media?.positionSec()
        val body = JSONObject()
            .put("url", url)
            .put("title", media?.title ?: "")
            .put("start_at", pos ?: JSONObject.NULL)
            .put("stop_on_end", true)
            .put("live", pos != null && media?.playing == true)

        busy = true
        renderBubble()
        toast(if (pos != null) "PC에서 ${fmtTime(pos.toLong())}부터 녹화 준비 중…" else "PC에서 녹화 준비 중…")
        scope.launch {
            try {
                val r = Remote.resolve(this@LinkService, host) ?: throw ApiException(0, "PC에 연결할 수 없습니다")
                // PC가 페이지를 열고 영상을 맞추는 동안 기다린다 (최대 1분)
                Api.request(r.base, "/api/record/start", "POST", body, host.token, timeoutMs = 60_000)
                recording = true
                syncPkg = pkg
                userPaused = false
                lastSyncAt = System.currentTimeMillis()
                toast("PC에서 녹화 중")
                RecordingService.watch(this@LinkService, host.id)
                startPolling(host)
            } catch (e: ApiException) {
                toast(e.message ?: "녹화를 시작하지 못했습니다")
            } catch (e: Exception) {
                toast("PC에 연결할 수 없습니다")
            }
            busy = false
            renderBubble()
            updateVisibility()
        }
    }

    // ---- 녹화 중: PC 상태 확인 + 폰 재생과 동기화 ----------------------------
    private fun startPolling(host: Host) {
        pollJob?.cancel()
        pollJob = scope.launch {
            var failures = 0
            while (isActive) {
                try {
                    val r = Remote.resolve(this@LinkService, host) ?: throw IllegalStateException()
                    val st = JSONObject(Api.request(r.base, "/api/status", token = host.token, timeoutMs = 4000))
                    failures = 0
                    val obs = st.optJSONObject("obs")
                    val nowRecording = obs?.optBoolean("recording") == true || st.optBoolean("starting")
                    if (!nowRecording) {
                        onRecordingEnded()
                        return@launch
                    }
                    pcPaused = obs?.optBoolean("paused") == true
                    durationMs = obs?.optLong("duration_ms") ?: 0
                    val player = st.optJSONObject("player")
                    pcTime = if (player?.optBoolean("found") == true) player.optDouble("time") else null
                    renderBubble()
                    updatePanel()
                    syncWithPhone(r.base, host.token)
                } catch (e: Exception) {
                    if (++failures >= 30) { // 30초 동안 연결 안 됨
                        toast("PC와 연결이 끊겼습니다")
                        onRecordingEnded()
                        return@launch
                    }
                }
                delay(1000)
            }
        }
    }

    private fun onRecordingEnded() {
        if (recording) toast("녹화를 저장했습니다")
        recording = false
        pcPaused = false
        syncPkg = null
        hidePanel()
        renderBubble()
        updateVisibility()
    }

    /** 폰에서 멈추면 PC도 멈추고, 재생하면 재생하고, 위치를 옮기면 PC도 옮긴다. */
    private suspend fun syncWithPhone(base: String, token: String) {
        val media = MediaLink.find(this, syncPkg) ?: return
        val now = System.currentTimeMillis()
        if (now - lastSyncAt < 2500) return // 방금 맞췄으면 잠시 대기 (반영 시간)
        val phonePos = media.positionSec()
        when {
            !media.playing && !pcPaused -> {
                Api.post(base, "/api/record/pause", token)
                lastSyncAt = now
            }
            media.playing && pcPaused && !userPaused -> {
                phonePos?.let { Api.post(base, "/api/player/seek", token, JSONObject().put("to", it)) }
                Api.post(base, "/api/record/resume", token)
                lastSyncAt = now
            }
            media.playing && !pcPaused && phonePos != null && pcTime != null && abs(phonePos - pcTime!!) > 5 -> {
                // 폰에서 앞뒤로 넘긴 경우
                Api.post(base, "/api/player/seek", token, JSONObject().put("to", phonePos + 0.3))
                lastSyncAt = now
            }
        }
    }

    // ---- 녹화 중 패널 (버튼을 누르면 펼쳐짐) -----------------------------------
    private fun togglePanel() {
        if (panel != null) hidePanel() else showPanel()
    }

    private fun panelButton(text: String, color: Int, onClick: () -> Unit) = TextView(this).apply {
        this.text = text
        setTextColor(color)
        textSize = 15f
        setTypeface(typeface, Typeface.BOLD)
        setPadding(dp(18), dp(12), dp(18), dp(12))
        setOnClickListener { onClick() }
    }

    private fun showPanel() {
        val p = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = round(0xF21B1E24.toInt())
            elevation = dp(8).toFloat()
            setPadding(dp(4), dp(8), dp(4), dp(8))
        }
        p.addView(TextView(this).apply {
            tag = "info"
            setTextColor(0xFF8A919E.toInt())
            textSize = 12f
            setPadding(dp(18), dp(4), dp(18), dp(6))
        })
        p.addView(panelButton("", 0xFFECEEF2.toInt()) { togglePause() }.apply { tag = "pause" })
        p.addView(panelButton("■ 정지하고 저장", 0xFFFF4D5E.toInt()) { stopRecording() })
        p.addView(panelButton("닫기", 0xFF8A919E.toInt()) { hidePanel() })
        val screenW = resources.displayMetrics.widthPixels
        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT, WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            val onRight = bubbleLp.x > screenW / 2
            x = if (onRight) maxOf(0, bubbleLp.x - dp(200)) else bubbleLp.x + dp(68)
            y = bubbleLp.y
        }
        wm.addView(p, lp)
        panel = p
        updatePanel()
    }

    private fun updatePanel() {
        val p = panel ?: return
        p.findViewWithTag<TextView>("info")?.text = buildString {
            append("PC 녹화 ").append(fmtTime(durationMs / 1000))
            pcTime?.let { append(" · 영상 ").append(fmtTime(it.toLong())) }
        }
        p.findViewWithTag<TextView>("pause")?.text = if (pcPaused) "▶ 같이 재생" else "❚❚ 같이 일시정지"
    }

    private fun hidePanel() {
        panel?.let { runCatching { wm.removeView(it) } }
        panel = null
    }

    private fun togglePause() {
        val host = HostStore(this).get(HostStore(this).lastHostId) ?: return
        val media = MediaLink.find(this, syncPkg)
        val pause = !pcPaused
        userPaused = pause
        // 폰 영상도 같이 멈추고/재생해서 둘이 어긋나지 않게
        if (pause) media?.pause() else media?.play()
        lastSyncAt = System.currentTimeMillis()
        scope.launch {
            try {
                val r = Remote.resolve(this@LinkService, host) ?: return@launch toast("PC에 연결할 수 없습니다")
                if (!pause) media?.positionSec()?.let {
                    Api.post(r.base, "/api/player/seek", host.token, JSONObject().put("to", it))
                }
                Api.post(r.base, if (pause) "/api/record/pause" else "/api/record/resume", host.token)
                pcPaused = pause
                renderBubble()
                updatePanel()
            } catch (e: Exception) {
                toast(e.message ?: "실패했습니다")
            }
        }
    }

    private fun stopRecording() {
        val host = HostStore(this).get(HostStore(this).lastHostId) ?: return
        hidePanel()
        busy = true
        renderBubble()
        scope.launch {
            try {
                val r = Remote.resolve(this@LinkService, host) ?: throw ApiException(0, "PC에 연결할 수 없습니다")
                Api.request(r.base, "/api/record/stop", "POST", JSONObject(), host.token, timeoutMs = 30_000)
                pollJob?.cancel()
                busy = false
                onRecordingEnded()
            } catch (e: Exception) {
                busy = false
                renderBubble()
                toast(e.message ?: "정지하지 못했습니다")
            }
        }
    }
}
