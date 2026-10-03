using System.Collections.Immutable;
using System.Reflection;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using System.Text;

var path = args.Length > 0 ? args[0] : throw new Exception("need dll path");
var wanted = args.Skip(1).ToImmutableArray();

using var fs = File.OpenRead(path);
using var pe = new PEReader(fs);
var md = pe.GetMetadataReader();
var prov = new Prov(md);

foreach (var th in md.TypeDefinitions)
{
    var td = md.GetTypeDefinition(th);
    var full = FullName(td);
    var nm = md.GetString(td.Name);
    if (wanted.Length > 0 && !wanted.Contains(nm) && !wanted.Contains(full))
        continue;

    Console.WriteLine($"=== {full}  : {BaseName(td.BaseType)}  vis={td.Attributes & TypeAttributes.VisibilityMask}");
    foreach (var fh in td.GetFields())
    {
        var f = md.GetFieldDefinition(fh);
        if (f.Attributes.HasFlag(FieldAttributes.Static)) continue;
        Console.WriteLine($"  field   {f.DecodeSignature(prov, null),-26} {md.GetString(f.Name)}  [{f.Attributes & FieldAttributes.FieldAccessMask}]");
    }
    foreach (var ph in td.GetProperties())
    {
        var p = md.GetPropertyDefinition(ph);
        var sig = p.DecodeSignature(prov, null);
        Console.WriteLine($"  property {sig.ReturnType,-26} {md.GetString(p.Name)}");
    }
    foreach (var mh in td.GetMethods())
    {
        var m = md.GetMethodDefinition(mh);
        var name = md.GetString(m.Name);
        if (name.StartsWith("get_") || name.StartsWith("set_") || name.StartsWith(".")) continue;
        var acc = (m.Attributes & MethodAttributes.MemberAccessMask) == MethodAttributes.Private ? "priv" : "pub ";
        Console.WriteLine($"  method {acc} {m.DecodeSignature(prov, null).ReturnType,-22} {name}()");
    }
}

string FullName(TypeDefinition td)
{
    var ns = md.GetString(td.Namespace);
    var nm = md.GetString(td.Name);
    return ns.Length == 0 ? nm : ns + "." + nm;
}

string BaseName(EntityHandle h)
{
    if (h.IsNil) return "-";
    return h.Kind switch
    {
        HandleKind.TypeDefinition => prov.GetTypeFromDefinition(md, (TypeDefinitionHandle)h, 0),
        HandleKind.TypeReference => prov.GetTypeFromReference(md, (TypeReferenceHandle)h, 0),
        _ => "?",
    };
}

if (args.Length > 1 && args[1] == "--fields")
{
    var names = args[2].Split(',').ToImmutableArray();
    foreach (var th in md.TypeDefinitions)
    {
        var td = md.GetTypeDefinition(th);
        foreach (var fh in td.GetFields())
        {
            var f = md.GetFieldDefinition(fh);
            var fn = md.GetString(f.Name);
            if (names.Contains(fn))
                Console.WriteLine($"{fn,-10} {f.DecodeSignature(prov, null),-14} base={BaseName(td.BaseType),-22} in {FullName(td)}");
        }
    }
    return;
}

sealed class Prov : ISignatureTypeProvider<string, object?>
{
    private readonly MetadataReader _md;
    public Prov(MetadataReader md) => _md = md;

    public string GetArrayType(string e, ArrayShape s) => e + "[]";
    public string GetByReferenceType(string e) => "ref " + e;
    public string GetFunctionPointerType(MethodSignature<string> s) => "fnptr";
    public string GetGenericInstantiation(string g, ImmutableArray<string> a) => g + "<" + string.Join(",", a) + ">";
    public string GetGenericMethodParameter(object? gc, int i) => "!!" + i;
    public string GetGenericTypeParameter(object? gc, int i) => "!" + i;
    public string GetModifiedType(string mod, string un, bool isRequired) => un;
    public string GetPinnedType(string e) => e;
    public string GetPointerType(string e) => e + "*";
    public string GetPrimitiveType(PrimitiveTypeCode c) => c.ToString().ToLowerInvariant();
    public string GetSZArrayType(string e) => e + "[]";
    public string GetSystemType() => "object";
    public bool IsSystemType(string t) => t == "object";
    public string GetTypeFromSerializedName(string name) => name.Split(',')[0];
    public PrimitiveTypeCode GetUnderlyingEnumType(string t) => PrimitiveTypeCode.Int32;

    public string GetTypeFromDefinition(MetadataReader md, TypeDefinitionHandle h, byte raw)
    {
        var td = md.GetTypeDefinition(h);
        var ns = md.GetString(td.Namespace);
        return ns.Length == 0 ? md.GetString(td.Name) : ns + "." + md.GetString(td.Name);
    }

    public string GetTypeFromReference(MetadataReader md, TypeReferenceHandle h, byte raw)
    {
        var tr = md.GetTypeReference(h);
        var ns = md.GetString(tr.Namespace);
        return ns.Length == 0 ? md.GetString(tr.Name) : ns + "." + md.GetString(tr.Name);
    }

    public string GetTypeFromSpecification(MetadataReader md, object? gc, TypeSpecificationHandle h, byte raw)
        => md.GetTypeSpecification(h).DecodeSignature(this, gc);
}
