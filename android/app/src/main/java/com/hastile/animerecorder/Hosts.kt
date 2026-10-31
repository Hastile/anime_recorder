package com.hastile.animerecorder

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/** 페어링된 PC. 주소는 여러 개(LAN, Tailscale 등)를 들고 있다가 되는 것으로 접속한다. */
data class Host(
    val id: String,
    val name: String,
    val port: Int,
    val addresses: List<String>,
    val token: String,
    val lastAddress: String? = null,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("id", id)
        .put("name", name)
        .put("port", port)
        .put("addresses", JSONArray(addresses))
        .put("token", token)
        .put("lastAddress", lastAddress ?: JSONObject.NULL)

    companion object {
        fun fromJson(o: JSONObject): Host {
            val a = o.optJSONArray("addresses") ?: JSONArray()
            return Host(
                id = o.getString("id"),
                name = o.optString("name", "PC"),
                port = o.optInt("port", 8765),
                addresses = List(a.length()) { a.getString(it) },
                token = o.getString("token"),
                lastAddress = if (o.isNull("lastAddress")) null else o.optString("lastAddress"),
            )
        }

        /** 서버의 /api/info 응답으로 Host를 만든다. 서버가 알려준 Tailscale 주소도 함께 저장된다. */
        fun fromInfo(info: JSONObject, reachedAddress: String, token: String): Host {
            val addrs = mutableListOf(reachedAddress)
            info.optJSONArray("addresses")?.let { a ->
                for (i in 0 until a.length()) addrs += a.getJSONObject(i).getString("ip")
            }
            return Host(
                id = info.getString("id"),
                name = info.optString("name", "PC"),
                port = info.optInt("port", 8765),
                addresses = addrs.distinct(),
                token = token,
                lastAddress = reachedAddress,
            )
        }
    }
}

class HostStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("hosts", Context.MODE_PRIVATE)

    fun all(): List<Host> {
        val a = JSONArray(prefs.getString("list", "[]"))
        return List(a.length()) { Host.fromJson(a.getJSONObject(it)) }
    }

    fun get(id: String?): Host? = id?.let { all().find { h -> h.id == id } }

    @Synchronized
    fun save(host: Host) {
        val list = all().toMutableList()
        val i = list.indexOfFirst { it.id == host.id }
        if (i >= 0) list[i] = host else list += host
        write(list)
    }

    @Synchronized
    fun remove(id: String) {
        write(all().filter { it.id != id })
        if (lastHostId == id) lastHostId = null
    }

    private fun write(list: List<Host>) {
        prefs.edit().putString("list", JSONArray(list.map { it.toJson() }).toString()).apply()
    }

    /** 마지막으로 연 PC (빠른 설정 타일, 알림에서 사용) */
    var lastHostId: String?
        get() = prefs.getString("last", null) ?: all().firstOrNull()?.id
        set(v) { prefs.edit().putString("last", v).apply() }
}

fun isRemoteAddress(addr: String): Boolean {
    if (addr.contains(".ts.net")) return true
    val parts = addr.removePrefix("http://").removePrefix("https://").split(".")
    val second = parts.getOrNull(1)?.toIntOrNull() ?: return false
    return parts[0] == "100" && second in 64..127
}

fun fmtTime(sec: Long): String {
    val s = maxOf(0, sec)
    val h = s / 3600
    val m = (s % 3600) / 60
    val r = s % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, r) else "%02d:%02d".format(m, r)
}
