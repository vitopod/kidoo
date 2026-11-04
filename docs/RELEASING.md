# Releasing Kidoo Community for TV

The official Community app uses application ID `org.kidoo.community.tv`. Keep its signing key private and backed up securely outside this repository. Android updates must use the same signing key; losing it means existing installs cannot be updated with a newly signed APK. Do not commit the keystore, its password, personal data, or local server settings. Kidoo Plus uses a separate package and release process.

The official Community signing certificate SHA-256 fingerprint is:

```text
4a2ce422f49c7f98fba2b425b6f3ebd48040d4b60acbab60c4ccef70b3bb4bd7
```

For a new release, increment both `versionCode` and `versionName`, run server and Android tests, and build `android-tv` with `./gradlew testDebugUnitTest assembleRelease` using Java 17 and the supported Android SDK. Align the unsigned APK with Android build-tools `zipalign`, then sign the aligned APK with `apksigner` using the private Community release key. Pass passwords through a protected environment or interactive prompt, never a committed file or command-line argument. Verify the signed APK with `apksigner verify --verbose --print-certs`, inspect its application ID and version with `aapt dump badging`, and check that the project licence and third-party notices are packaged under `assets/licenses/`.

Publish the exact reviewed source commit as a versioned GitHub release, attach the signed APK and a `SHA256SUMS.txt` file, and include the Community licence. Note any hardware or device tests not performed. Keep an offline private backup of the signing key and password; never publish them. The public certificate fingerprint above is safe to share.
