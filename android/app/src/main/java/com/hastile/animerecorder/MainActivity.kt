package com.hastile.animerecorder

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LifecycleResumeEffect
import android.provider.Settings
import kotlinx.coroutines.launch
import org.json.JSONObject

private val Bg = Color(0xFF111317)
private val CardBg = Color(0xFF1B1E24)
private val Muted = Color(0xFF8A919E)
private val Green = Color(0xFF3CCF8E)
private val Gold = Color(0xFFFFC247)
private val Red = Color(0xFFFF4D5E)
private val Blue = Color(0xFF4D9BFF)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { AppTheme { HostListScreen() } }
    }
}

@Composable
fun AppTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Blue, onPrimary = Color.White,
            background = Bg, surface = Bg, surfaceVariant = CardBg,
            onBackground = Color(0xFFECEEF2), onSurface = Color(0xFFECEEF2),
            secondaryContainer = Color(0xFF263449), onSecondaryContainer = Color.White,
        ),
        content = content,
    )
}

private sealed interface HostState {
    data object Checking : HostState
    data class Online(val address: String) : HostState
    data object Offline : HostState
}

/** 페어링할 대상: 접속된 주소와 PC 정보 */
private data class PairTarget(val base: String, val address: String, val info: JSONObject)

fun openControl(context: Context, hostId: String) {
    HostStore(context).lastHostId = hostId
    context.startActivity(Intent(context, ControlActivity::class.java).putExtra(ControlActivity.EXTRA_HOST, hostId))
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun HostListScreen() {
    val ctx = LocalContext.current
    val store = remember { HostStore(ctx) }
    val scope = rememberCoroutineScope()
    var hosts by remember { mutableStateOf(store.all()) }
    val states = remember { mutableStateMapOf<String, HostState>() }
    var found by remember { mutableStateOf<List<Discovery.Found>>(emptyList()) }
    var scanning by remember { mutableStateOf(false) }
    var pairTarget by remember { mutableStateOf<PairTarget?>(null) }
    var showAdd by remember { mutableStateOf(false) }
    var menuFor by remember { mutableStateOf<Host?>(null) }

    fun refresh() {
        hosts = store.all()
        scope.launch {
            scanning = true
            for (h in hosts) {
                states[h.id] = HostState.Checking
                launch {
                    val r = Remote.resolve(ctx, h, useCache = false)
                    states[h.id] = if (r != null) HostState.Online(r.address) else HostState.Offline
                }
            }
            found = Discovery.scan(ctx)
            scanning = false
            hosts = store.all()
        }
    }

    val notifPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}
    LaunchedEffect(Unit) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        refresh()
    }

    Scaffold(
        containerColor = Bg,
        topBar = {
            TopAppBar(
                title = { Text("Anime Recorder", fontWeight = FontWeight.Bold) },
                actions = {
                    IconButton(onClick = { refresh() }) { Icon(Icons.Filled.Refresh, contentDescription = "새로고침") }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Bg),
            )
        },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = { showAdd = true },
                icon = { Icon(Icons.Filled.Add, contentDescription = null) },
                text = { Text("PC 추가") },
                containerColor = Blue, contentColor = Color.White,
            )
        },
    ) { pad ->
        LazyColumn(
            contentPadding = PaddingValues(
                start = 16.dp, end = 16.dp,
                top = pad.calculateTopPadding() + 8.dp,
                bottom = pad.calculateBottomPadding() + 96.dp,
            ),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item { LinkSetupCard() }
            item { SectionTitle("내 PC") }
            if (hosts.isEmpty()) {
                item { Hint("아직 연결된 PC가 없습니다.\n아래에서 찾은 PC를 누르거나 'PC 추가'를 누르세요.") }
            }
            items(hosts, key = { it.id }) { h ->
                HostCard(
                    host = h,
                    state = states[h.id],
                    onClick = { openControl(ctx, h.id) },
                    onLongClick = { menuFor = h },
                )
            }
            item {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    SectionTitle("같은 와이파이에서 찾은 PC")
                    Spacer(Modifier.width(10.dp))
                    if (scanning) CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                }
            }
            val unpaired = found.filter { f -> hosts.none { it.id == f.id } }
            if (unpaired.isEmpty() && !scanning) {
                item { Hint("찾은 PC가 없습니다. PC에서 서버(start.bat)가 실행 중인지, 같은 와이파이인지 확인하세요.") }
            }
            items(unpaired, key = { "f-" + it.id }) { f ->
                FoundCard(f) { pairTarget = PairTarget(Api.baseUrl(f.ip, f.port), f.ip, f.info) }
            }
        }
    }

    pairTarget?.let { t ->
        PairDialog(
            target = t,
            onDismiss = { pairTarget = null },
            onPaired = { host ->
                store.save(host)
                pairTarget = null
                refresh()
                openControl(ctx, host.id)
            },
        )
    }
    if (showAdd) {
        AddDialog(
            onDismiss = { showAdd = false },
            onPair = { showAdd = false; pairTarget = it },
            onSaved = { host -> showAdd = false; store.save(host); refresh() },
        )
    }
    menuFor?.let { h ->
        HostMenuDialog(
            host = h,
            onDismiss = { menuFor = null },
            onSave = { store.save(it); Remote.invalidate(it.id); menuFor = null; refresh() },
            onDelete = { store.remove(h.id); menuFor = null; refresh() },
        )
    }
}

/** 브라우저 연동(플로팅 버튼) 권한 안내 */
@Composable
private fun LinkSetupCard() {
    val ctx = LocalContext.current
    var linkOn by remember { mutableStateOf(LinkService.isEnabled(ctx)) }
    var mediaOn by remember { mutableStateOf(MediaLink.isEnabled(ctx)) }
    LifecycleResumeEffect(Unit) {
        linkOn = LinkService.isEnabled(ctx)
        mediaOn = MediaLink.isEnabled(ctx)
        onPauseOrDispose { }
    }
    fun open(action: String) {
        runCatching { ctx.startActivity(Intent(action).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
    }
    Card(
        colors = CardDefaults.cardColors(containerColor = if (linkOn && mediaOn) CardBg else Color(0xFF1D2A3D)),
        shape = RoundedCornerShape(16.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("브라우저 연동", fontWeight = FontWeight.Bold, fontSize = 17.sp)
            if (linkOn && mediaOn) {
                Text("✓ 준비 완료. 폰 브라우저에서 영상을 보다가 화면 위 ● REC 버튼을 누르면 PC가 같은 페이지를 같은 위치부터 녹화합니다.",
                    color = Muted, fontSize = 14.sp)
                return@Column
            }
            Text("폰 브라우저로 보다가 화면 위 ● 버튼만 누르면, PC가 같은 페이지를 같은 위치부터 녹화하고 일시정지·이동도 따라갑니다.",
                color = Muted, fontSize = 14.sp)
            PermissionRow("① 플로팅 버튼 + 주소 읽기", "접근성 → 설치된 앱 → Anime Recorder 연동", linkOn) {
                open(Settings.ACTION_ACCESSIBILITY_SETTINGS)
            }
            PermissionRow("② 재생 위치·일시정지 연동", "알림 접근 → Anime Recorder", mediaOn) {
                open(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
            }
            Text("설정이 회색으로 막혀 있으면: 설정 → 애플리케이션 → Anime Recorder → 오른쪽 위 ⋮ → '제한된 설정 허용' 후 다시 켜세요.",
                color = Muted, fontSize = 12.sp)
        }
    }
}

@Composable
private fun PermissionRow(title: String, where: String, on: Boolean, onOpen: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
            Text(if (on) "켜짐" else where, color = if (on) Green else Muted, fontSize = 12.sp)
        }
        if (!on) FilledTonalButton(onClick = onOpen) { Text("켜기") }
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, color = Muted, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 8.dp))
}

@Composable
private fun Hint(text: String) {
    Text(text, color = Muted, fontSize = 14.sp, modifier = Modifier.padding(vertical = 8.dp))
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun HostCard(host: Host, state: HostState?, onClick: () -> Unit, onLongClick: () -> Unit) {
    val (dot, label) = when (state) {
        is HostState.Online -> Green to (if (isRemoteAddress(state.address)) "온라인 · 외부(Tailscale)" else "온라인 · 같은 와이파이")
        HostState.Offline -> Red to "연결 안 됨"
        else -> Gold to "확인 중…"
    }
    Card(
        colors = CardDefaults.cardColors(containerColor = CardBg),
        shape = RoundedCornerShape(16.dp),
        modifier = Modifier.fillMaxWidth().combinedClickable(onClick = onClick, onLongClick = onLongClick),
    ) {
        Row(Modifier.padding(18.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier.size(48.dp).background(Color(0xFF263449), RoundedCornerShape(12.dp)),
                contentAlignment = Alignment.Center,
            ) { Text("PC", fontWeight = FontWeight.Bold, color = Blue) }
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text(host.name, fontWeight = FontWeight.Bold, fontSize = 17.sp)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(8.dp).background(dot, CircleShape))
                    Spacer(Modifier.width(6.dp))
                    Text(label, color = Muted, fontSize = 13.sp)
                }
            }
            Text("›", color = Muted, fontSize = 28.sp)
        }
    }
}

@Composable
private fun FoundCard(f: Discovery.Found, onPair: () -> Unit) {
    Card(
        colors = CardDefaults.cardColors(containerColor = CardBg),
        shape = RoundedCornerShape(16.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(Modifier.padding(18.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(f.name, fontWeight = FontWeight.Bold, fontSize = 17.sp)
                Text(f.ip, color = Muted, fontSize = 13.sp)
            }
            FilledTonalButton(onClick = onPair) { Text("연결") }
        }
    }
}

@Composable
private fun PairDialog(target: PairTarget, onDismiss: () -> Unit, onPaired: (Host) -> Unit) {
    val scope = rememberCoroutineScope()
    var pairId by remember { mutableStateOf<String?>(null) }
    var pin by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }

    fun start() {
        error = null
        scope.launch {
            try {
                val r = Api.request(target.base, "/api/pair/start", "POST",
                    JSONObject().put("device", "${Build.MANUFACTURER} ${Build.MODEL}"))
                pairId = JSONObject(r).getString("pair_id")
            } catch (e: Exception) {
                error = e.message ?: "PC에 연결할 수 없습니다"
            }
        }
    }

    fun confirm() {
        val id = pairId ?: return
        busy = true
        error = null
        scope.launch {
            try {
                val r = JSONObject(Api.request(target.base, "/api/pair/confirm", "POST",
                    JSONObject().put("pair_id", id).put("pin", pin)))
                onPaired(Host.fromInfo(r.getJSONObject("host"), target.address, r.getString("token")))
            } catch (e: ApiException) {
                error = e.message
                pin = ""
                if (e.message?.contains("만료") == true || e.message?.contains("취소") == true) pairId = null
            } catch (e: Exception) {
                error = "PC에 연결할 수 없습니다"
            }
            busy = false
        }
    }

    LaunchedEffect(Unit) { start() }

    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = CardBg,
        title = { Text("${target.info.optString("name", "PC")} 연결") },
        text = {
            Column {
                Text("PC 화면에 뜬 4자리 PIN을 입력하세요.", color = Muted)
                Spacer(Modifier.height(16.dp))
                OutlinedTextField(
                    value = pin,
                    onValueChange = { v ->
                        pin = v.filter { it.isDigit() }.take(4)
                        if (pin.length == 4 && !busy) confirm()
                    },
                    enabled = pairId != null && !busy,
                    singleLine = true,
                    placeholder = { Text(if (pairId == null) "요청 중…" else "0000") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                    textStyle = MaterialTheme.typography.headlineMedium.copy(textAlign = TextAlign.Center, letterSpacing = 12.sp),
                    modifier = Modifier.fillMaxWidth(),
                )
                error?.let {
                    Spacer(Modifier.height(10.dp))
                    Text(it, color = Red, fontSize = 14.sp)
                }
            }
        },
        confirmButton = {
            if (pairId == null && error != null) TextButton(onClick = { start() }) { Text("다시 요청") }
            else TextButton(onClick = { confirm() }, enabled = pin.length == 4 && !busy) { Text("연결") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("취소") } },
    )
}

@Composable
private fun AddDialog(onDismiss: () -> Unit, onPair: (PairTarget) -> Unit, onSaved: (Host) -> Unit) {
    val scope = rememberCoroutineScope()
    var address by remember { mutableStateOf("") }
    var port by remember { mutableStateOf("8765") }
    var token by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }

    fun connect() {
        busy = true
        error = null
        scope.launch {
            val addr = address.trim()
            val base = Api.baseUrl(addr, port.toIntOrNull() ?: 8765)
            val info = Api.info(base, 5000)
            if (info == null) {
                error = "PC를 찾을 수 없습니다. 주소와 서버 실행 여부를 확인하세요."
            } else if (token.isBlank()) {
                onPair(PairTarget(base, addr, info))
            } else {
                try {
                    Api.request(base, "/api/status", token = token.trim())
                    onSaved(Host.fromInfo(info, addr, token.trim()))
                } catch (e: Exception) {
                    error = e.message ?: "토큰이 올바르지 않습니다"
                }
            }
            busy = false
        }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = CardBg,
        title = { Text("PC 추가") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("PC의 IP, Tailscale 주소(100.x.x.x 또는 이름.ts.net) 또는 https 주소", color = Muted, fontSize = 13.sp)
                OutlinedTextField(address, { address = it }, label = { Text("주소") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri), modifier = Modifier.fillMaxWidth())
                OutlinedTextField(port, { port = it.filter(Char::isDigit) }, label = { Text("포트") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.fillMaxWidth())
                OutlinedTextField(token, { token = it }, label = { Text("접속 토큰 (선택, 비우면 PIN으로 연결)") },
                    singleLine = true, modifier = Modifier.fillMaxWidth())
                error?.let { Text(it, color = Red, fontSize = 14.sp) }
            }
        },
        confirmButton = {
            TextButton(onClick = { connect() }, enabled = address.isNotBlank() && !busy) {
                Text(if (busy) "확인 중…" else "연결")
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("취소") } },
    )
}

@Composable
private fun HostMenuDialog(host: Host, onDismiss: () -> Unit, onSave: (Host) -> Unit, onDelete: () -> Unit) {
    var name by remember { mutableStateOf(host.name) }
    var newAddr by remember { mutableStateOf("") }
    var confirmDelete by remember { mutableStateOf(false) }

    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = CardBg,
        title = { Text(host.name) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(name, { name = it }, label = { Text("이름") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Text("저장된 주소 (되는 주소로 자동 접속)", color = Muted, fontSize = 13.sp)
                host.addresses.forEach { a ->
                    Text("• $a" + if (isRemoteAddress(a)) "  (외부)" else "", fontSize = 14.sp)
                }
                OutlinedTextField(newAddr, { newAddr = it }, label = { Text("주소 추가 (예: Tailscale 주소)") },
                    singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                    modifier = Modifier.fillMaxWidth())
                TextButton(onClick = { if (confirmDelete) onDelete() else confirmDelete = true }) {
                    Text(if (confirmDelete) "정말 삭제하기" else "이 PC 삭제", color = Red)
                }
            }
        },
        confirmButton = {
            TextButton(onClick = {
                val addrs = if (newAddr.isBlank()) host.addresses else (host.addresses + newAddr.trim()).distinct()
                onSave(host.copy(name = name.ifBlank { host.name }, addresses = addrs))
            }) { Text("저장") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("닫기") } },
    )
}
