# Android release dependency and asset review

**Technical evidence, not legal clearance.** Generated 3 November 2026 for the current local Kidoo TV 0.2.0 unsigned Android release; it may differ from a future signed release or other platform. `assembleRelease` and `testDebugUnitTest` passed. A release-specific Gradle artifact resolution returned 46 artifact records representing 45 unique component coordinates. Cached POM/parent-POM metadata declared Apache 2.0 for each coordinate; that is not independent verification of every artifact's contents or all obligations. Build plugins, test-only dependencies, Android platform components and separately installed optional tools are outside this runtime list.

The rebuilt unsigned APK has SHA-256 `4529b94014a972715d3d4c5fa1fd154cb3df35182b8169a4d6b87adccbc2587a` and was inspected for the following paths:

- `assets/licenses/Outfit-OFL.txt`
- `assets/licenses/Apache-2.0.txt`
- `assets/licenses/MPL-2.0.txt`
- `assets/licenses/THIRD-PARTY-NOTICES.txt`
- `okhttp3/internal/publicsuffix/NOTICE`
- `okhttp3/internal/publicsuffix/publicsuffixes.gz`

OkHttp 4.12.0's exact Public Suffix List source is identified in the packaged third-party notice. The source rules were reconstructed and matched against both the release JAR and APK resource. The release runtime inventory is available as [CycloneDX JSON](third-party/android-release-runtime.cdx.json); its licence fields reflect Maven metadata and are not a complete legal conclusion. See the [third-party review](THIRD-PARTY-REVIEW.md) for evidence, scope and remaining release work.

## Release runtime components

| Coordinate | Licence as declared by cached POM metadata |
| --- | --- |
| `androidx.annotation:annotation-experimental:1.4.0` | The Apache Software License, Version 2.0 |
| `androidx.annotation:annotation-jvm:1.8.1` | The Apache Software License, Version 2.0 |
| `androidx.arch.core:core-common:2.2.0` | The Apache Software License, Version 2.0 |
| `androidx.arch.core:core-runtime:2.2.0` | The Apache Software License, Version 2.0 |
| `androidx.collection:collection-jvm:1.4.2` | The Apache Software License, Version 2.0 |
| `androidx.concurrent:concurrent-futures:1.2.0` | The Apache Software License, Version 2.0 |
| `androidx.core:core:1.13.0` | The Apache Software License, Version 2.0 |
| `androidx.core:core-ktx:1.13.0` | The Apache Software License, Version 2.0 |
| `androidx.customview:customview:1.0.0` | The Apache Software License, Version 2.0 |
| `androidx.customview:customview-poolingcontainer:1.0.0` | The Apache Software License, Version 2.0 |
| `androidx.exifinterface:exifinterface:1.3.6` | The Apache Software License, Version 2.0 |
| `androidx.interpolator:interpolator:1.0.0` | The Apache Software License, Version 2.0 |
| `androidx.leanback:leanback-grid:1.0.0` | The Apache Software License, Version 2.0 |
| `androidx.lifecycle:lifecycle-common-jvm:2.8.0` | The Apache Software License, Version 2.0 |
| `androidx.lifecycle:lifecycle-runtime-android:2.8.0` | The Apache Software License, Version 2.0 |
| `androidx.lifecycle:lifecycle-service:2.8.0` | The Apache Software License, Version 2.0 |
| `androidx.media:media:1.7.0` | The Apache Software License, Version 2.0 |
| `androidx.media3:media3-common:1.11.1` | The Apache Software License, Version 2.0 |
| `androidx.media3:media3-container:1.11.1` | The Apache Software License, Version 2.0 |
| `androidx.media3:media3-database:1.11.1` | The Apache Software License, Version 2.0 |
| `androidx.media3:media3-datasource:1.11.1` | The Apache Software License, Version 2.0 |
| `androidx.media3:media3-datasource-okhttp:1.11.1` | The Apache Software License, Version 2.0 |
| `androidx.media3:media3-decoder:1.11.1` | The Apache Software License, Version 2.0 |
| `androidx.media3:media3-exoplayer:1.11.1` | The Apache Software License, Version 2.0 |
| `androidx.media3:media3-exoplayer-hls:1.11.1` | The Apache Software License, Version 2.0 |
| `androidx.media3:media3-extractor:1.11.1` | The Apache Software License, Version 2.0 |
| `androidx.media3:media3-session:1.11.1` | The Apache Software License, Version 2.0 |
| `androidx.media3:media3-ui:1.11.1` | The Apache Software License, Version 2.0 |
| `androidx.profileinstaller:profileinstaller:1.3.1` | The Apache Software License, Version 2.0 |
| `androidx.recyclerview:recyclerview:1.4.0` | The Apache Software License, Version 2.0 |
| `androidx.startup:startup-runtime:1.1.1` | The Apache Software License, Version 2.0 |
| `androidx.tracing:tracing:1.0.0` | The Apache Software License, Version 2.0 |
| `androidx.versionedparcelable:versionedparcelable:1.1.1` | The Apache Software License, Version 2.0 |
| `com.google.guava:failureaccess:1.0.2` | The Apache Software License, Version 2.0 (parent POM) |
| `com.google.guava:guava:33.3.1-android` | Apache License, Version 2.0 (parent POM) |
| `com.google.guava:listenablefuture:9999.0-empty-to-avoid-conflict-with-guava` | The Apache Software License, Version 2.0 (parent POM) |
| `com.squareup.okhttp3:okhttp:4.12.0` | The Apache Software License, Version 2.0 |
| `com.squareup.okio:okio-jvm:3.6.0` | The Apache Software License, Version 2.0 |
| `org.jetbrains:annotations:23.0.0` | The Apache Software License, Version 2.0 |
| `org.jetbrains.kotlin:kotlin-stdlib:2.2.21` | Apache-2.0 |
| `org.jetbrains.kotlin:kotlin-stdlib-jdk7:1.9.10` | The Apache License, Version 2.0 |
| `org.jetbrains.kotlin:kotlin-stdlib-jdk8:1.9.10` | The Apache License, Version 2.0 |
| `org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3` | The Apache Software License, Version 2.0 |
| `org.jetbrains.kotlinx:kotlinx-coroutines-core-jvm:1.7.3` | The Apache Software License, Version 2.0 |
| `org.jspecify:jspecify:1.0.0` | The Apache License, Version 2.0 |

## Remaining release work

- Regenerate and verify the dependency inventory and notices against each actual signed release. The current runtime inventory excludes app assets, build and test dependencies, and the Android platform.
- Make the packaged notice and the identified matching MPL source accessible to every recipient of an executable, and maintain the source availability required by MPL 2.0. Recheck the source match when OkHttp changes.
- Verify release-specific transitive notices and licences, source-distribution and build dependencies, and the exact licences and source obligations of any separately bundled tools. Consult counsel before release where appropriate.
- The owner reports creating the Kidoo logo; trademark clearance and any separate third-party elements have not been independently established. The old repository and its pull-request references are private, but previously public images and code may remain in third-party copies or caches.
- The Community licence has been adopted; it does not grant rights to third-party media or waive obligations under their licences.
