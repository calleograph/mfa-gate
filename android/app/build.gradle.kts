import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

// Версия приложения — из файла VERSION в корне android/ (своя нумерация, не версия сервера).
val appVersion = rootProject.file("VERSION").readText().trim()
val versionParts = appVersion.split(".").map { it.toInt() }
val signing = Properties().apply {
    // Ключи подписи — только из окружения или локального файла вне Git (см. README.md).
    val f = rootProject.file("signing.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
fun sign(name: String, env: String): String? = System.getenv(env) ?: signing.getProperty(name)

android {
    namespace = "io.github.calleograph.mfagate"
    compileSdk = 37

    defaultConfig {
        applicationId = "io.github.calleograph.mfagate"
        minSdk = 26
        targetSdk = 36
        // смещение нужно только проверке «обновление поверх»: предыдущая сборка той же версии с меньшим versionCode
        versionCode = versionParts[0] * 10000 + versionParts[1] * 100 + versionParts[2] + ((findProperty("versionCodeOffset") as String?)?.toInt() ?: 0)
        versionName = appVersion
    }

    flavorDimensions += "push"
    productFlavors {
        // Без сервисов Google: long-poll на открытом экране и проверка WorkManager каждые 15 минут.
        create("foss") { dimension = "push"; buildConfigField("String", "PUSH_PROVIDER", "\"none\"") }
        // Firebase Cloud Messaging; параметры проекта Firebase приходят с сервера, google-services.json не нужен.
        create("gms") { dimension = "push"; buildConfigField("String", "PUSH_PROVIDER", "\"fcm\"") }
    }

    signingConfigs {
        val store = sign("storeFile", "MFA_GATE_KEYSTORE")
        if (store != null) {
            create("release") {
                storeFile = file(store)
                storePassword = sign("storePassword", "MFA_GATE_KEYSTORE_PASSWORD")
                keyAlias = sign("keyAlias", "MFA_GATE_KEY_ALIAS")
                keyPassword = sign("keyPassword", "MFA_GATE_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            // Без ключа организации сборка подписывается отладочным ключом: годится для проверки, не для публикации.
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }

    buildFeatures { compose = true; buildConfig = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    packaging { resources.excludes += setOf("/META-INF/{AL2.0,LGPL2.1}", "META-INF/versions/9/OSGI-INF/MANIFEST.MF") }
    lint { abortOnError = true; warningsAsErrors = false; checkReleaseBuilds = true }
    testOptions { unitTests.isReturnDefaultValues = true; unitTests.isIncludeAndroidResources = true }
}

dependencies {
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.material3)
    implementation(libs.compose.icons)
    debugImplementation(libs.compose.tooling)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.fragment)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.biometric)
    implementation(libs.androidx.work)
    implementation(libs.zxing.embedded)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)
    "gmsImplementation"(platform(libs.firebase.bom))
    "gmsImplementation"(libs.firebase.messaging)
    testImplementation(libs.junit)
    testImplementation(libs.kotlin.test.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.kotlinx.coroutines.test)
}
