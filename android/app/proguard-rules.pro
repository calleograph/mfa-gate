# kotlinx.serialization: сериализаторы ищутся через Companion.
-keep class kotlinx.serialization.** { *; }
-keepclassmembers class io.github.calleograph.mfagate.** { *** Companion; }
-keepclasseswithmembers class io.github.calleograph.mfagate.** { kotlinx.serialization.KSerializer serializer(...); }
# OkHttp 5: необязательные TLS-провайдеры.
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**
