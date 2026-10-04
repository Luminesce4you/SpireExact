"""One short native clock-adapter check; no STS2 search or seed is launched."""
from pathlib import Path
import argparse,json,os,subprocess,sys
from xml.sax.saxutils import escape

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from spire_exact.native import dotnet as find_dotnet

PROGRAM=r'''
using System.Diagnostics;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Text.Json;
using SpireNativeHost;
namespace PauseClockValidation;
class Program
{
    [DllImport("kernel32.dll")] static extern bool QueryPerformanceCounter(out long ticks);
    [DllImport("kernel32.dll")] static extern bool QueryPerformanceFrequency(out long ticks);
    static double Raw() { QueryPerformanceCounter(out var ticks); QueryPerformanceFrequency(out var frequency); return (double)ticks/frequency; }
    static void Ledger(string path,double total,double? started) => File.WriteAllText(path,JsonSerializer.Serialize(new {schema="spire-pause/v1",paused_total_seconds=total,paused_started_monotonic=started}));
    [MethodImpl(MethodImplOptions.NoInlining)] static Stopwatch CreateClock() => Stopwatch.StartNew();
    [MethodImpl(MethodImplOptions.NoInlining)] static double Budget(Stopwatch watch) => watch.Elapsed.TotalSeconds;
    [MethodImpl(MethodImplOptions.NoInlining)] static DateTime DeadlineNow() => DateTime.UtcNow;
    [MethodImpl(MethodImplOptions.NoInlining)] static void RestartClock(Stopwatch watch) => watch.Restart();
    static int Main(string[] args)
    {
        string path=args[0];Ledger(path,0,null);Environment.SetEnvironmentVariable("SPIRE_PAUSE_LEDGER",path);
        PauseClock.Install(Assembly.GetExecutingAssembly());
        var watch=CreateClock();DateTime before=DeadlineNow();
        Thread.Sleep(70);double activeBefore=Budget(watch);
        double pauseStart=Raw();Ledger(path,0,pauseStart);
        Thread.Sleep(350);double during=Budget(watch);
        double total=Raw()-pauseStart;Ledger(path,total,null);
        Thread.Sleep(35);double after=Budget(watch);double dateActive=(DeadlineNow()-before).TotalSeconds;
        RestartClock(watch);Thread.Sleep(60);double restarted=Budget(watch);
        bool passed=Math.Abs(during-activeBefore)<.06 && after-activeBefore>.015 && after-activeBefore<.12
            && dateActive<.25 && restarted>.03 && restarted<.15;
        Console.WriteLine(JsonSerializer.Serialize(new {passed,active_before=activeBefore,during_pause=during,after_resume=after,
            utc_active_seconds=dateActive,restarted_seconds=restarted,wall_pause_seconds=total,adapter=PauseClock.Metrics(),game_searches=0}));
        return passed?0:1;
    }
}
'''


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--dotnet',type=Path)
    parser.add_argument('--harmony',type=Path,required=True);args=parser.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    work=ROOT/'.tools/pause-clock-check';work.mkdir(parents=True,exist_ok=True)
    (work/'Program.cs').write_text(PROGRAM,encoding='utf-8')
    project=f'''<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net9.0</TargetFramework><AssemblyName>OfflineSearchHarness</AssemblyName><ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable></PropertyGroup><ItemGroup><Compile Include="{escape(str(ROOT/'native/SpireNativeHost/PauseClock.cs'))}" Link="PauseClock.cs"/><Reference Include="0Harmony" HintPath="{escape(str(args.harmony.resolve()))}"/></ItemGroup></Project>'''
    (work/'ClockCheck.csproj').write_text(project,encoding='utf-8')
    dotnet=str(args.dotnet.resolve()) if args.dotnet else find_dotnet()
    build=subprocess.run([dotnet,'build',str(work/'ClockCheck.csproj'),'-c','Release','--nologo'],capture_output=True,text=True,encoding='utf-8',errors='replace')
    (out/'build.log').write_text(build.stdout+build.stderr,encoding='utf-8')
    if build.returncode:raise SystemExit('Clock helper build failed; see build.log')
    result=subprocess.run([dotnet,str(work/'bin/Release/net9.0/OfflineSearchHarness.dll'),str(work/'pause-ledger.json')],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
    (out/'native.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    lines=result.stdout.strip().splitlines()
    if not lines:raise SystemExit('Clock helper failed; see native.log')
    report=json.loads(lines[-1])
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(report);raise SystemExit(result.returncode)


if __name__=='__main__':main()
