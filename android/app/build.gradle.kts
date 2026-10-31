plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.hastile.animerecorder"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.hastile.animerecorder"
        minSdk = 26
        targetSdk = 35
        // CI 빌드 번호로 자동 증가 → 새 APK가 기존 앱 위에 업데이트 설치됨
        versionCode = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
        versionName = "0.2.${System.getenv("GITHUB_RUN_NUMBER") ?: "0"}"
    }

    // 서명 키는 저장소에 두지 않는다. CI가 환경 변수로 넘겨준다 (.github/workflows/android.yml).
    // 매 빌드 같은 키로 서명해야 새 APK가 기존 앱 위에 업데이트 설치된다.
    val storePath = System.getenv("SIGNING_STORE_FILE")
    signingConfigs {
        if (storePath != null) {
            create("ci") {
                storeFile = file(storePath)
                storePassword = System.getenv("SIGNING_STORE_PASSWORD")
                keyAlias = System.getenv("SIGNING_KEY_ALIAS") ?: "release"
                keyPassword = System.getenv("SIGNING_STORE_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // 키가 없으면(로컬 빌드) 디버그 키로 서명
            signingConfig = if (storePath != null) signingConfigs.getByName("ci") else signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.12.01")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
}
