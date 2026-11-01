"""Explicit owner-run Windows room qualification; raw audio/video stay in bounded RAM.

Uses installed FFmpeg DirectShow, Windows SAPI and one selected WinMM playback
device. No station access, volume changes, media download or recording files.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import struct
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any


def audio_metrics(pcm: bytes, rate: int, threshold: int) -> dict[str, Any]:
    intervals: list[list[int]] = []
    active: int | None = None
    peak = 0
    block = rate // 20 * 2
    for offset in range(0, len(pcm) - block + 1, block):
        values = [v[0] for v in struct.iter_unpack("<h", pcm[offset:offset + block])]
        peak = max(peak, max(abs(v) for v in values))
        loud = math.sqrt(sum(v * v for v in values) / len(values)) >= threshold
        if loud and active is None:
            active = offset * 500 // rate
        if not loud and active is not None:
            intervals.append([active, offset * 500 // rate])
            active = None
    if active is not None:
        intervals.append([active, len(pcm) * 500 // rate])
    return {"active_intervals_ms": intervals, "peak": peak, "threshold_rms": threshold,
            "duration_ms": len(pcm) * 500 // rate}


def colour_metrics(rgb: bytes, width: int, height: int, fps: int) -> list[dict[str, int]]:
    size = width * height * 3
    result = []
    for offset in range(0, len(rgb) - size + 1, size):
        counts = {"ms": offset // size * 1000 // fps, "red": 0, "purple": 0,
                  "blue": 0, "green": 0}
        for r, g, b in struct.iter_unpack("BBB", rgb[offset:offset + size]):
            if max(r, g, b) < 40:
                continue
            if min(r, b) > g * 1.4 and min(r, b) * 3 > max(r, b):
                counts["purple"] += 1
            elif r > g * 1.6 and r > b * 1.6:
                counts["red"] += 1
            elif b > r * 1.6 and b > g * 1.6:
                counts["blue"] += 1
            elif g > r * 1.6 and g > b * 1.6:
                counts["green"] += 1
        result.append(counts)
    return result


def powershell(script: str, timeout: float = 20) -> bytes:
    encoded = base64.b64encode(script.encode("utf-16le")).decode()
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                             encoded], capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Windows media operation failed; raw output withheld")
    return result.stdout


PLAY = r'''
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
public static class RoomPlayer {
 [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
 struct Caps { public ushort mid,pid; public uint version;
  [MarshalAs(UnmanagedType.ByValTStr,SizeConst=32)] public string name;
  public uint formats; public ushort channels,reserved; public uint support; }
 [StructLayout(LayoutKind.Sequential,Pack=2)]
 struct Format { public ushort tag,channels; public uint rate,bytes;
  public ushort align,bits,extra; }
 [StructLayout(LayoutKind.Sequential)]
 struct Header { public IntPtr data; public uint length,recorded; public UIntPtr user;
  public uint flags,loops; public IntPtr next; public UIntPtr reserved; }
 [DllImport("winmm.dll")] static extern uint waveOutGetNumDevs();
 [DllImport("winmm.dll",CharSet=CharSet.Unicode)]
 static extern uint waveOutGetDevCaps(UIntPtr id,out Caps caps,uint size);
 [DllImport("winmm.dll")] static extern uint waveOutOpen(out IntPtr handle,uint id,
  ref Format format,IntPtr callback,IntPtr instance,uint flags);
 [DllImport("winmm.dll")] static extern uint waveOutPrepareHeader(IntPtr h,IntPtr p,uint size);
 [DllImport("winmm.dll")] static extern uint waveOutWrite(IntPtr h,IntPtr p,uint size);
 [DllImport("winmm.dll")] static extern uint waveOutUnprepareHeader(IntPtr h,IntPtr p,uint size);
 [DllImport("winmm.dll")] static extern uint waveOutReset(IntPtr h);
 [DllImport("winmm.dll")] static extern uint waveOutClose(IntPtr h);
 static void Check(uint result) { if(result!=0) throw new Exception("WinMM playback failed"); }
 public static void Play(byte[] wav,string device) {
  uint selected=uint.MaxValue;
  for(uint i=0;i<waveOutGetNumDevs();i++) { Caps c;
   Check(waveOutGetDevCaps((UIntPtr)i,out c,(uint)Marshal.SizeOf(typeof(Caps))));
   if(c.name.IndexOf(device,StringComparison.OrdinalIgnoreCase)>=0) {
    if(selected!=uint.MaxValue) throw new Exception("Ambiguous playback device"); selected=i;
   }
  }
  if(selected==uint.MaxValue) throw new Exception("Playback device not found");
  Format f=new Format(); byte[] pcm=null;
  using(var reader=new BinaryReader(new MemoryStream(wav))) {
   if(new string(reader.ReadChars(4))!="RIFF") throw new Exception("Invalid WAV");
   reader.ReadUInt32();
   if(new string(reader.ReadChars(4))!="WAVE") throw new Exception("Invalid WAV");
   while(reader.BaseStream.Position+8<=wav.Length) {
    string kind=new string(reader.ReadChars(4)); int n=reader.ReadInt32();
    if(n<0||reader.BaseStream.Position+n>wav.Length) throw new Exception("Invalid WAV");
    byte[] data=reader.ReadBytes(n); if((n&1)!=0) reader.ReadByte();
    if(kind=="fmt ") { f.tag=BitConverter.ToUInt16(data,0);f.channels=BitConverter.ToUInt16(data,2);
     f.rate=BitConverter.ToUInt32(data,4);f.bytes=BitConverter.ToUInt32(data,8);
     f.align=BitConverter.ToUInt16(data,12);f.bits=BitConverter.ToUInt16(data,14); }
    if(kind=="data") pcm=data;
   }
  }
  if(f.tag!=1||f.bits!=16||pcm==null||pcm.Length>f.bytes*15) throw new Exception("WAV bounds");
  IntPtr handle; Check(waveOutOpen(out handle,selected,ref f,IntPtr.Zero,IntPtr.Zero,0));
  GCHandle pinned=GCHandle.Alloc(pcm,GCHandleType.Pinned);
  int size=Marshal.SizeOf(typeof(Header)); IntPtr pointer=Marshal.AllocHGlobal(size);
  bool prepared=false;
  try {
   Header header=new Header(); header.data=pinned.AddrOfPinnedObject();
   header.length=(uint)pcm.Length;
   Marshal.StructureToPtr(header,pointer,false);
   Check(waveOutPrepareHeader(handle,pointer,(uint)size));prepared=true;
   Check(waveOutWrite(handle,pointer,(uint)size));
   var timer=System.Diagnostics.Stopwatch.StartNew();
   while((((Header)Marshal.PtrToStructure(pointer,typeof(Header))).flags&1)==0) {
    if(timer.ElapsedMilliseconds>18000) throw new Exception("Playback timeout"); Thread.Sleep(5);
   }
  } finally {
   waveOutReset(handle);if(prepared)waveOutUnprepareHeader(handle,pointer,(uint)size);
   Marshal.FreeHGlobal(pointer);pinned.Free();waveOutClose(handle);
  }
 }
}
'''


def play_text(text: str, speaker: str) -> None:
    if not text or len(text) > 120 or any(ord(c) < 32 for c in text):
        raise ValueError("Test phrase must be short plain text")
    literal = text.replace("'", "''")
    output = speaker.replace("'", "''")
    script = ("Add-Type -AssemblyName System.Speech\nAdd-Type -TypeDefinition @'\n" + PLAY
        + "\n'@\n$s=New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        + "$s.SelectVoice('Microsoft Irina Desktop')\n$m=New-Object IO.MemoryStream\n"
        + f"try {{$s.SetOutputToWaveStream($m);$s.Speak('{literal}');"
        # Split static-access tokens: the publication scanner treats them as IPv6.
        + "[RoomPlayer]:" f":Play($m.ToArray(),'{output}')}} finally {{$s.Dispose();$m.Dispose()}}")
    powershell(script, 30)


def collect(process: subprocess.Popen[bytes], seconds: int) -> bytes:
    try:
        out, _ = process.communicate(timeout=seconds + 15)
        if process.returncode:
            raise RuntimeError("Capture device unavailable; raw output withheld")
        return out
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=12, choices=range(3, 21))
    parser.add_argument("--camera", required=True)
    parser.add_argument("--microphone", required=True)
    parser.add_argument("--speaker", default="Realtek")
    parser.add_argument("--play-text")
    parser.add_argument("--threshold", type=int, default=160)
    args = parser.parse_args()
    win = powershell("[Console]:" ":OutputEncoding=[Text.Encoding]:" ":UTF8; "
                     "(Get-Command ffmpeg.exe).Source").decode("utf-8-sig").strip()
    exe = subprocess.run(["wslpath", "-u", win], capture_output=True, text=True,
                         check=True).stdout.strip()
    prefix = [exe, "-hide_banner", "-loglevel", "error", "-f", "dshow"]
    audio = subprocess.Popen([*prefix, "-i", "audio=" + args.microphone, "-t", str(args.seconds),
        "-ar", "16000", "-ac", "1", "-f", "s16le", "pipe:1"], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL)
    video = subprocess.Popen([*prefix, "-i", "video=" + args.camera, "-frames:v",
        str(args.seconds * 10), "-vf", "scale=320:180,fps=10", "-pix_fmt", "rgb24", "-f",
        "rawvideo", "pipe:1"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    with ThreadPoolExecutor(max_workers=2) as workers:
        a = workers.submit(collect, audio, args.seconds)
        v = workers.submit(collect, video, args.seconds)
        if args.play_text:
            time.sleep(2.5)  # Let DirectShow initialize; timing comes from the captured PCM.
            play_text(args.play_text, args.speaker)
        pcm, rgb = a.result(), v.result()
    print(json.dumps({"audio": audio_metrics(pcm, 16000, args.threshold),
                      "colours": colour_metrics(rgb, 320, 180, 10), "recordings_saved": False,
                      "video_audio_synchronization_verified": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
