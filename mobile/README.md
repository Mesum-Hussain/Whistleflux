# Whistleflux for Android

A voice keyboard that opens on a big mic: tap it, speak, tap the pill, and your words are
typed into whatever app you're in. The keyboard button switches to a Gboard-style QWERTY
(swipe typing, suggestions that learn on the phone, symbols, emoji with search, clipboard).

Speech is recognized on the phone with Cactus Whistle (17 MB), the same model as the
desktop app, which ships inside the app (no download after installing). The text then gets
the same basic clean-up as on desktop, and optionally Gemini's smart clean-up (only the
text is sent, never audio).

The setup screen is React Native. The keyboard, recording, speech model and clean-up are
native Kotlin, because Android keyboards must be native services.

```
App.tsx                        setup screen: permissions, settings, try-it box
specs/NativeWhistleflux.ts     interface between App.tsx and the Kotlin code
android/app/src/main/java/app/whistleflux/
  ime/WhistlefluxKeyboard.kt   the keyboard service: typing, suggestions, voice
  ime/KeyboardView.kt          draws the keyboard and turns touches into keys, swipes,
                               trackpad moves; the mic key grows into the voice pill
  ime/Layouts.kt               mic view, QWERTY, symbols and number pad
  ime/Dictionary.kt            English word list (AOSP, Apache 2.0) for suggestions
  ime/GestureDecoder.kt        swipe typing: matches the swipe's path to words
  ime/PillRenderer.kt          the voice pill (same look as the desktop's)
  ime/KeyboardTheme.kt         Material 3 colors (Material You on Android 12+)
  engine/Dictation.kt          record -> transcribe -> clean up
  engine/Recorder.kt           16 kHz mic recording
  engine/SpeechEngine.kt       Whistle via Cactus's Needle engine, read from the app's assets
  engine/ModelManager.kt       where the bundled model lives
  engine/Cleanup.kt            basic clean-up rules (same as src/whistleflux/cleanup.py)
  engine/Formatter.kt          Gemini smart clean-up
  engine/Prompt.kt             Gemini prompt (the desktop app uses S1-mini instead)
  engine/Settings.kt           API key and options
  bridge/                      exposes the above to React Native
android/app/src/main/cpp/      JNI bridge to the speech engine (speech_jni.c) and its CMake build
```

## Build and run

Needs Node 22+, JDK 17 and the Android SDK (platform 37, build-tools 37.0.0,
NDK 27.1.12297006, CMake 3.22.1), with `JAVA_HOME` and `ANDROID_HOME` set.

1. On the phone, turn on **Developer options** (tap *Build number* 7 times in
   *Settings → About phone*), then **USB debugging**. Plug it in and accept the prompt.
2. Check it's visible: `adb devices`
3. Install and run:

   ```sh
   npm install
   npm start          # JavaScript dev server; leave it running
   npm run android    # in a second terminal: builds and installs the app
   ```

   This debug build loads its screen from the dev server, so it only opens while
   `npm start` is running and the phone is connected. To install a copy that works on
   its own (about 35 MB, signed with the debug key):

   ```sh
   cd android && ./gradlew :app:assembleRelease
   adb install -r app/build/outputs/apk/release/app-release.apk
   ```

The first build downloads Cactus's speech engine (2 MB) into `android/app/libs/`, and the
speech model (17 MB) and AOSP's English word list (0.9 MB) into `android/.speech-model/`,
all checked against their SHA-256.

The app is built for 64-bit ARM phones only (`reactNativeArchitectures` in
`android/gradle.properties`). Cactus only publishes the speech engine for arm64, so the
app doesn't run on x86_64 emulators.

## Tests

```sh
npm test           # setup screen renders
cd android && ./gradlew :app:testDebugUnitTest   # clean-up matches shared/cleanup-cases.json
npx tsc --noEmit   # type check
```

On a connected phone, the speech engine test transcribes a short clip through the JNI
bridge. Install with `adb install -r` rather than `connectedAndroidTest`, which uninstalls
the app (and its settings) afterwards:

```sh
cd android && ./gradlew :app:assembleDebug :app:assembleDebugAndroidTest
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
adb shell am instrument -w app.whistleflux.test/androidx.test.runner.AndroidJUnitRunner
```
