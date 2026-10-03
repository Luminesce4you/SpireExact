using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.Json;
using Microsoft.Diagnostics.Tracing;

if (args.Length == 4 && args[0] == "--aggregate")
{
    PmuAggregate.Run(args[1], args[2], args[3]);
    return;
}
if (args.Length==1 && (args[0]=="--describe" || args[0]=="--describe-threads"))
{
    foreach(var type in typeof(TraceEvent).Assembly.GetTypes().Where(t=>args[0]=="--describe-threads" ? t.Name.Contains("ThreadTraceData") || t.Name.Contains("ThreadSetName") : t.Name.Contains("PMC",StringComparison.OrdinalIgnoreCase)||t.Name.Contains("CSwitch")||t==typeof(TraceEvent)))
    {
        Console.WriteLine(type.FullName);
        foreach(var p in type.GetProperties(BindingFlags.Public|BindingFlags.Instance|BindingFlags.DeclaredOnly))Console.WriteLine("PROPERTY "+p);
        foreach(var m in type.GetMethods(BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Instance|BindingFlags.DeclaredOnly).Where(m=>m.Name.Contains("PMC",StringComparison.OrdinalIgnoreCase)||m.Name.Contains("Counter")||m.Name.Contains("Extended")))Console.WriteLine("METHOD "+m);
        if(type==typeof(TraceEvent))foreach(var f in type.GetFields(BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Instance|BindingFlags.DeclaredOnly))Console.WriteLine("FIELD "+f);
    }
    return;
}
if(args.Length!=2)throw new ArgumentException("capture.etl inspection.json");
var names=new Dictionary<string,long>();var examples=new List<object>();int cs=0,pmcSamples=0;long withCounters=0;
var prev=new Dictionary<int,ulong[]>();long decreases=0;
using var source=new ETWTraceEventSource(args[0]);
source.Kernel.All+=_=>{}; // register classic kernel templates, including CSwitch and PMC metadata
source.AllEvents+=data=>
{
    string key=data.ProviderName+"/"+data.EventName;
    names[key]=names.GetValueOrDefault(key)+1;
    ulong[] counters=key.Contains("CSwitch")?NativePmc.Read(data):[];
    if(counters.Length>0)withCounters++;
    if(counters.Length>0)
    {
        if(prev.TryGetValue(data.ProcessorNumber,out var old))
            if(counters.Zip(old).Any(v=>v.First<v.Second))decreases++;
        prev[data.ProcessorNumber]=counters;
    }
    bool metadata=data.TaskGuid==new Guid("ce1dbfb4-137e-4da6-87b0-3f59aa102cbc")&&((int)data.Opcode==48||(int)data.Opcode==49);
    if((metadata||key.Contains("PMC",StringComparison.OrdinalIgnoreCase)||key.Contains("CounterConfig")||key.Contains("CSwitch")&&cs++<2||counters.Length>0&&data.ProcessorNumber==8&&pmcSamples++<6)&&examples.Count<50)
    {
        var payload=new Dictionary<string,object?>();
        foreach(string name in data.PayloadNames)payload[name]=data.PayloadByName(name);
        byte[] raw=new byte[data.EventDataLength];Marshal.Copy(data.DataStart,raw,0,raw.Length);
        examples.Add(new{key,type=data.GetType().FullName,data.ProcessID,data.ThreadID,data.ProcessorNumber,data.TimeStampRelativeMSec,payload,counters,raw=Convert.ToHexString(raw),xml=data.ToString()});
    }
};
source.Process();
File.WriteAllText(args[1],JsonSerializer.Serialize(new{source.EventsLost,source.SessionStartTime,source.SessionEndTime,names,withCounters,decreases,examples},new JsonSerializerOptions{WriteIndented=true}));
Console.WriteLine(JsonSerializer.Serialize(new{source.EventsLost,events=names.Values.Sum(),withCounters,decreases,examples=examples.Count}));

static class NativePmc
{
    static readonly FieldInfo Record=typeof(TraceEvent).GetField("eventRecord",BindingFlags.NonPublic|BindingFlags.Instance)??throw new InvalidOperationException("TraceEvent native contract missing");
    static NativePmc()
    {
        var t=Record.FieldType.GetElementType()!;
        if(Marshal.OffsetOf(t,"ExtendedDataCount").ToInt32()!=84||Marshal.OffsetOf(t,"ExtendedData").ToInt32()!=88)
            throw new InvalidOperationException("x64 EVENT_RECORD layout changed");
    }
    public static unsafe ulong[] Read(TraceEvent value)
    {
        IntPtr rec=(IntPtr)Pointer.Unbox(Record.GetValue(value)!);
        int count=(ushort)Marshal.ReadInt16(rec,84);IntPtr items=Marshal.ReadIntPtr(rec,88);
        if(count>128)throw new InvalidOperationException("Invalid extended item count");
        for(int i=0;i<count;i++)
        {
            IntPtr item=items+i*16;
            if((ushort)Marshal.ReadInt16(item,2)!=8)continue; // EVENT_HEADER_EXT_TYPE_PMC_COUNTERS
            int bytes=(ushort)Marshal.ReadInt16(item,6);
            if(bytes==0||bytes%8!=0||bytes>128)throw new InvalidOperationException("Invalid PMC extended data length");
            IntPtr pointer=(IntPtr)Marshal.ReadInt64(item,8);ulong[] values=new ulong[bytes/8];
            for(int n=0;n<values.Length;n++)values[n]=unchecked((ulong)Marshal.ReadInt64(pointer,n*8));
            return values;
        }
        return [];
    }
}
