# kotlinx.serialization: сериализаторы ищутся через Companion.
-keep class kotlinx.serialization.** { *; }
-keepclassmembers class twofold.authenticator.** { *** Companion; }
-keepclasseswithmembers class twofold.authenticator.** { kotlinx.serialization.KSerializer serializer(...); }
# OkHttp 5: необязательные TLS-провайдеры.
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**
