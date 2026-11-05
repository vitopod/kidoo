# Install Kidoo Community on Fire TV

Kidoo Community is a separate Android TV app for your own protected Kidoo Community server. It runs on Android-based Fire TV devices with Android API 23 or newer; it does not run on Vega OS. It does not contain Kidoo Plus or YouTube features. The Community package is `org.kidoo.community.tv`, so it can be installed alongside an existing Kidoo Plus app without replacing its data.

## Before you start

1. Set up and start the protected Community server. Its HTTPS hostname and certificate must be trusted and reachable by the Fire TV. The TV does not need Tailscale if it can reach the server by another permitted route. See the [setup guide](SETUP.md).
2. Download the **Kidoo Community TV 0.3.0 APK** and `SHA256SUMS.txt` from the [release page](https://github.com/vitopod/kidoo/releases/tag/v0.3.0) to your computer. Download only from the project's release page. The release is not distributed through the Amazon Appstore.
3. Install Google's [Android SDK Platform Tools](https://developer.android.com/tools/releases/platform-tools) on the computer. This supplies `adb`; Android Studio is not required. Open a terminal in the extracted `platform-tools` folder. On macOS/Linux, prefix the commands below with `./` if `adb` is not on your PATH; on Windows use `adb.exe`.
4. Optionally verify the downloaded file's SHA-256 against `SHA256SUMS.txt`: run `shasum -a 256 kidoo-community-tv-0.3.0.apk` on macOS, `sha256sum kidoo-community-tv-0.3.0.apk` on Linux, or `certutil -hashfile kidoo-community-tv-0.3.0.apk SHA256` in Windows Command Prompt. Run the command in the APK's folder or give its full path. Compare it to the release checksum.

## Install over your home network

1. On the Fire TV, open **Settings → My Fire TV → About → Network** and note its IP address. Keep the TV and computer on the same network.
2. Open **Settings → My Fire TV → Developer options** and turn on **ADB debugging**. On some Fire OS versions, enable the hidden menu by selecting the device name under **About** seven times. Menu names vary; see Amazon's [ADB connection guide](https://developer.amazon.com/docs/fire-tv/connecting-adb-to-device.html). Approve the computer's debugging prompt on the TV when it appears.
3. In the terminal, replace `FIRE_TV_IP` with the address shown on the TV, and replace the APK path with the file you downloaded:

   ```sh
   adb connect FIRE_TV_IP:5555
   adb -s FIRE_TV_IP:5555 install -r /path/to/kidoo-community-tv-0.3.0.apk
   ```

   Quote the APK path if it contains spaces. On Windows, use the local Windows path. The `-s` option ensures the install targets that Fire TV if other Android devices are connected. A successful install reports `Success`.
4. Open **Kidoo Community** from the Fire TV app list. In the parent dashboard, choose the child profile and create a one-time pairing code. Enter the server's HTTPS address and the code in the app. If the certificate hostname does not resolve to the server on the TV's network, the app can use a private IPv4 route while still verifying the certificate hostname.
5. After installation, disconnect with `adb disconnect FIRE_TV_IP:5555` and turn **ADB debugging** off on the Fire TV. Do not expose ADB to the internet.

Before testing playback, set the TV or connected sound system to its minimum volume. If someone else is performing the test and cannot control the volume, lower it for them before playback begins.

Updates must be signed with the same Community release key. Install later official Community releases using the same `adb install -r` command. An unrelated APK with a different signing key cannot update this installation; don't uninstall an existing app just to bypass a signing mismatch without first understanding the data loss. The package cannot update Kidoo Plus.

This exact release APK was installed and paired on a physical Fire TV against an isolated test server. Local video playback and saving a position were verified; resuming that position was not tested in this check. If installation or pairing fails, check the server address, certificate trust, Fire OS compatibility and [Amazon's ADB troubleshooting](https://developer.amazon.com/docs/fire-tv/connecting-adb-to-device.html). The server and TV app must be kept on compatible Community versions.
