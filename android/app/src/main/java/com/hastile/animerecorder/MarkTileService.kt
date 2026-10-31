package com.hastile.animerecorder

import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import org.json.JSONObject

/** 빠른 설정 타일: 상단바를 내리고 탭하면 마지막으로 연 PC에 ★ 장면 표시. */
class MarkTileService : TileService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    override fun onStartListening() {
        val host = HostStore(this).get(HostStore(this).lastHostId) ?: return setTile(false, "PC 없음")
        scope.launch {
            val r = Remote.resolve(this@MarkTileService, host) ?: return@launch setTile(false, "연결 안 됨")
            try {
                val st = JSONObject(Api.request(r.base, "/api/status", token = host.token, timeoutMs = 3000))
                val rec = st.optJSONObject("obs")?.optBoolean("recording") == true
                setTile(rec, if (rec) "녹화 중 · 탭해서 표시" else "녹화 안 함")
            } catch (e: Exception) {
                setTile(false, "연결 안 됨")
            }
        }
    }

    override fun onClick() {
        val host = HostStore(this).get(HostStore(this).lastHostId) ?: return setTile(false, "앱에서 PC를 먼저 연결하세요")
        setTile(true, "표시 중…")
        scope.launch {
            val r = Remote.resolve(this@MarkTileService, host) ?: return@launch setTile(false, "연결 안 됨")
            try {
                val m = JSONObject(Api.post(r.base, "/api/mark", host.token, JSONObject().put("kind", "point")))
                setTile(true, "★ ${fmtTime(m.optDouble("t").toLong())} 표시됨")
            } catch (e: ApiException) {
                setTile(false, e.message ?: "실패")
            } catch (e: Exception) {
                setTile(false, "연결 안 됨")
            }
        }
    }

    private fun setTile(active: Boolean, subtitle: String) {
        val t = qsTile ?: return
        t.state = if (active) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
        if (Build.VERSION.SDK_INT >= 29) t.subtitle = subtitle
        t.updateTile()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }
}
