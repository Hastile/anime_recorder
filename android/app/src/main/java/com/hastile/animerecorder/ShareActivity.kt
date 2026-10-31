package com.hastile.animerecorder

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.widget.Toast

/**
 * 폰 브라우저에서 "공유 → PC에서 녹화"를 누르면 호출된다.
 * 공유된 주소를 들고 마지막으로 쓴 PC의 녹화 화면을 연다 (주소, 제목이 미리 채워짐).
 */
class ShareActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val text = intent.getStringExtra(Intent.EXTRA_TEXT).orEmpty()
        val subject = intent.getStringExtra(Intent.EXTRA_SUBJECT).orEmpty()
        val url = Regex("""https?://\S+""").find(text)?.value

        val store = HostStore(this)
        val host = store.get(store.lastHostId)
        when {
            url == null -> Toast.makeText(this, "공유된 내용에서 주소를 찾지 못했습니다", Toast.LENGTH_LONG).show()
            host == null -> {
                Toast.makeText(this, "먼저 앱에서 PC를 연결하세요", Toast.LENGTH_LONG).show()
                startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
            else -> startActivity(
                Intent(this, ControlActivity::class.java)
                    .putExtra(ControlActivity.EXTRA_HOST, host.id)
                    .putExtra(ControlActivity.EXTRA_SHARE_URL, url)
                    .putExtra(ControlActivity.EXTRA_SHARE_TITLE, subject.ifBlank { text.replace(url, "").trim() })
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            )
        }
        finish()
    }
}
