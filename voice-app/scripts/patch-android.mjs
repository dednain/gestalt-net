import fs from 'node:fs';
const manifest = 'android/app/src/main/AndroidManifest.xml';
let xml = fs.readFileSync(manifest, 'utf8');
for (const permission of ['android.permission.RECORD_AUDIO','android.permission.MODIFY_AUDIO_SETTINGS']) {
  if (!xml.includes('android:name="' + permission + '"')) {
    xml = xml.replace('<application', '    <uses-permission android:name="' + permission + '" />\n    <application');
  }
}
fs.writeFileSync(manifest, xml);
console.log('Added microphone permissions to Android manifest');
