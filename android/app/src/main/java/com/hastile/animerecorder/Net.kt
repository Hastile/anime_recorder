package com.hastile.animerecorder

import android.content.Context
import android.net.ConnectivityManager
import android.os.SystemClock
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.InetAddress
import java.net.SocketTimeoutException
import java.net.URL
import java.nio.ByteBuffer
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

class ApiException(val code: Int, message: String) : Exception(message)

object Api {
    fun baseUrl(address: String, port: Int): String {
        val a = address.trim().trimEnd('/')
        return when {
            a.startsWith("http://") || a.startsWith("https://") -> a
            a.contains(':') && !a.contains('.') -> "http://[$a]:$port" // IPv6
            else -> "http://$a:$port"
        }
    }

    suspend fun request(
        base: String,
        path: String,
        method: String = "GET",
        body: JSONObject? = null,
        token: String? = null,
        timeoutMs: Int = 5000,
    ): String = withContext(Dispatchers.IO) {
        val conn = (URL(base + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = timeoutMs
            readTimeout = timeoutMs
            token?.let { setRequestProperty("Authorization", "Bearer $it") }
            if (body != null) {
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
            }
        }
        try {
            if (body != null) conn.outputStream.use { it.write(body.toString().toByteArray()) }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader()?.use { it.readText() } ?: ""
            if (code !in 200..299) {
                val detail = runCatching { JSONObject(text).optString("detail") }.getOrNull()
                throw ApiException(code, detail?.takeIf { it.isNotBlank() } ?: "HTTP $code")
            }
            text
        } finally {
            conn.disconnect()
        }
    }

    suspend fun post(base: String, path: String, token: String, body: JSONObject = JSONObject()) =
        request(base, path, "POST", body, token)

    /** 해당 주소가 Anime Recorder 서버면 PC 정보를, 아니면 null. */
    suspend fun info(base: String, timeoutMs: Int = 2500): JSONObject? =
        runCatching { JSONObject(request(base, "/api/info", timeoutMs = timeoutMs)) }
            .getOrNull()
            ?.takeIf { it.optString("app") == "anime-recorder" }
}

/** PC의 여러 주소 중 지금 접속되는 주소를 찾는다 (LAN이든 Tailscale이든). */
object Remote {
    data class Resolved(val base: String, val address: String)

    private val io = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val cache = ConcurrentHashMap<String, Pair<Resolved, Long>>()

    fun invalidate(hostId: String) {
        cache.remove(hostId)
    }

    suspend fun resolve(context: Context, host: Host, useCache: Boolean = true): Resolved? {
        if (useCache) {
            cache[host.id]?.let { (r, at) -> if (SystemClock.elapsedRealtime() - at < 30_000) return r }
        }
        val candidates = (listOfNotNull(host.lastAddress) + host.addresses).distinct()
        if (candidates.isEmpty()) return null
        val result = CompletableDeferred<Resolved?>()
        val remaining = AtomicInteger(candidates.size)
        for (addr in candidates) {
            io.launch {
                val base = Api.baseUrl(addr, host.port)
                val info = Api.info(base)
                // 같은 IP에 다른 PC가 있을 수 있으니 PC id까지 확인
                if (info != null && info.optString("id") == host.id) result.complete(Resolved(base, addr))
                if (remaining.decrementAndGet() == 0) result.complete(null)
            }
        }
        val r = withTimeoutOrNull(6000) { result.await() }
        if (r != null) {
            cache[host.id] = r to SystemClock.elapsedRealtime()
            if (r.address != host.lastAddress) {
                HostStore(context).get(host.id)?.let { HostStore(context).save(it.copy(lastAddress = r.address)) }
            }
        } else {
            cache.remove(host.id)
        }
        return r
    }
}

/** 같은 와이파이에서 PC 자동 검색 (서버의 UDP 8766에 브로드캐스트). */
object Discovery {
    private const val PORT = 8766
    private val MAGIC = "ANIME_RECORDER_DISCOVER_V1".toByteArray()

    data class Found(val ip: String, val info: JSONObject) {
        val id: String get() = info.optString("id")
        val name: String get() = info.optString("name", ip)
        val port: Int get() = info.optInt("port", 8765)
    }

    suspend fun scan(context: Context, timeoutMs: Long = 1800): List<Found> = withContext(Dispatchers.IO) {
        val found = LinkedHashMap<String, Found>()
        runCatching {
            DatagramSocket().use { s ->
                s.broadcast = true
                s.soTimeout = 300
                val targets = linkedSetOf<InetAddress>(InetAddress.getByName("255.255.255.255"))
                targets += subnetBroadcasts(context)
                repeat(2) {
                    for (t in targets) runCatching { s.send(DatagramPacket(MAGIC, MAGIC.size, t, PORT)) }
                }
                val buf = ByteArray(8192)
                val end = SystemClock.elapsedRealtime() + timeoutMs
                while (SystemClock.elapsedRealtime() < end) {
                    val p = DatagramPacket(buf, buf.size)
                    try {
                        s.receive(p)
                    } catch (e: SocketTimeoutException) {
                        continue
                    }
                    val info = runCatching { JSONObject(String(p.data, 0, p.length)) }.getOrNull() ?: continue
                    if (info.optString("app") != "anime-recorder") continue
                    val ip = p.address.hostAddress ?: continue
                    found.putIfAbsent(info.optString("id"), Found(ip, info))
                }
            }
        }
        found.values.toList()
    }

    @Suppress("DEPRECATION")
    private fun subnetBroadcasts(context: Context): List<InetAddress> {
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return emptyList()
        return cm.allNetworks.flatMap { net ->
            val lp = cm.getLinkProperties(net) ?: return@flatMap emptyList()
            lp.linkAddresses.mapNotNull { la ->
                val a = la.address as? Inet4Address ?: return@mapNotNull null
                if (la.prefixLength !in 8..30) return@mapNotNull null
                val ip = ByteBuffer.wrap(a.address).int
                val mask = -1 shl (32 - la.prefixLength)
                InetAddress.getByAddress(ByteBuffer.allocate(4).putInt(ip or mask.inv()).array())
            }
        }
    }
}
