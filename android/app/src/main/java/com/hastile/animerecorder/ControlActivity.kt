package com.hastile.animerecorder

import android.annotation.SuppressLint
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.enableEdgeToEdge
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import org.json.JSONObject

/** PC의 녹화 화면. 서버가 제공하는 리모컨 화면을 그대로 띄우고, 녹화가 시작되면 알림 리모컨을 켠다. */
class ControlActivity : ComponentActivity() {
    companion object {
        const val EXTRA_HOST = "host"
    }

    private lateinit var web: WebView
    private lateinit var host: Host
    private var base: String? = null
    private var wasRecording = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        host = HostStore(this).get(intent.getStringExtra(EXTRA_HOST)) ?: run { finish(); return }

        web = WebView(this).apply {
            setBackgroundColor(Color.parseColor("#111317"))
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.mediaPlaybackRequiresUserGesture = false
            webChromeClient = WebChromeClient() // confirm()/prompt() 대화상자 표시
            webViewClient = object : WebViewClient() {
                override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                    if (request.isForMainFrame) showError("PC에 연결할 수 없습니다")
                }

                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    val b = base ?: return false
                    if (request.url.toString().startsWith(b)) return false
                    // 녹화 서버 밖의 링크는 일반 브라우저로
                    runCatching { startActivity(Intent(Intent.ACTION_VIEW, request.url)) }
                    return true
                }
            }
            addJavascriptInterface(Bridge(), "AnimeRecorderApp")
        }
        // 상태바/내비게이션 바에 가리지 않게
        ViewCompat.setOnApplyWindowInsetsListener(web) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }
        setContentView(web)

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.canGoBack()) web.goBack() else finish()
            }
        })
        connect()
    }

    private fun connect() {
        showMessage("${host.name}에 연결 중…", retry = false)
        lifecycleScope.launch {
            val r = Remote.resolve(this@ControlActivity, host, useCache = false)
            if (r == null) {
                showError("${host.name}에 연결할 수 없습니다.\nPC의 서버가 켜져 있는지, 밖이라면 Tailscale이 켜져 있는지 확인하세요.")
                return@launch
            }
            base = r.base
            HostStore(this@ControlActivity).lastHostId = host.id
            web.loadUrl("${r.base}/#token=${Uri.encode(host.token)}")
        }
    }

    private fun showError(msg: String) = showMessage(msg, retry = true)

    private fun showMessage(msg: String, retry: Boolean) {
        val safe = msg.replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br>")
        val button = if (retry) """<button onclick="AnimeRecorderApp.retry()">다시 연결</button>""" else ""
        val html = """
            <html><head><meta name="viewport" content="width=device-width,initial-scale=1">
            <style>body{background:#111317;color:#eceef2;font-family:sans-serif;display:flex;flex-direction:column;
            align-items:center;justify-content:center;height:90vh;text-align:center;padding:24px;line-height:1.6}
            button{margin-top:24px;padding:14px 28px;border:0;border-radius:14px;background:#4d9bff;color:#fff;font-size:17px}</style>
            </head><body><div>$safe</div>$button</body></html>
        """.trimIndent()
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
    }

    override fun onDestroy() {
        if (::web.isInitialized) web.destroy()
        super.onDestroy()
    }

    /** 웹 화면(app.js)에서 호출 */
    inner class Bridge {
        @JavascriptInterface
        fun onStatus(json: String) {
            val recording = runCatching { JSONObject(json).optBoolean("recording") }.getOrDefault(false)
            runOnUiThread {
                if (recording && !wasRecording) RecordingService.watch(this@ControlActivity, host.id)
                wasRecording = recording
            }
        }

        @JavascriptInterface
        fun retry() {
            runOnUiThread { connect() }
        }
    }
}
