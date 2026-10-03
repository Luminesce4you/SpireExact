using System.Buffers.Binary;
using System.Reflection;
using System.Reflection.Emit;
using System.Reflection.Metadata;
using System.Reflection.Metadata.Ecma335;
using System.Reflection.PortableExecutable;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace SpireNativeHost;

// Reads PE metadata and CIL; it never loads or executes the inspected assembly.
// Metadata token indices, RVAs, MVIDs, debug records, paths and PDBs are excluded.
// Every member and nested generated type of the guarded class is included.
internal static class SolverContractFingerprint
{
    public const string Schema = "spire-solver-contract-cil/v1";
    public const string GuardedType = "CombatSolver.CorePowerSupport";
    // Independently read from the approved holdout-01 0AF01356... assembly and
    // the clean pinned-source rebuild: all 3 types / 23 methods match exactly.
    public const string Expected = "D654B44B786C7B1ED0103521BD72ACC9D47ADC5A030985593FF2D471792B3E0E";

    public static string Compute(string assemblyPath) => Inspect(assemblyPath).Sha256;

    public static (string Sha256, string Canonical, int Types, int Methods) Inspect(string assemblyPath)
    {
        using var stream = File.OpenRead(assemblyPath);
        using var pe = new PEReader(stream);
        if (!pe.HasMetadata) throw new BadImageFormatException("Solver has no CLI metadata");
        var reader = pe.GetMetadataReader();
        var context = new Context(reader, pe);
        var selected = reader.TypeDefinitions.Where(handle => context.IsGuarded(handle))
            .OrderBy(context.TypeName, StringComparer.Ordinal).ToArray();
        if (selected.Length == 0 || !selected.Any(handle => context.TypeName(handle) == GuardedType))
            throw new InvalidDataException("Guarded solver type not found");
        var text = new StringBuilder();
        void Line(object value) => text.Append(JsonSerializer.Serialize(value)).Append('\n');
        Line(new { schema = Schema, type = GuardedType });
        int methods = 0;
        foreach (var handle in selected)
        {
            var type = reader.GetTypeDefinition(handle);
            var layout = type.GetLayout();
            Line(new { kind = "type", name = context.TypeName(handle), attributes = (int)type.Attributes,
                base_type = context.Entity(type.BaseType), layout = new { layout.PackingSize, layout.Size },
                generic = context.Generics(type.GetGenericParameters()), custom = context.Custom(type.GetCustomAttributes()),
                interfaces = type.GetInterfaceImplementations().Select(x => context.Entity(reader.GetInterfaceImplementation(x).Interface))
                    .OrderBy(x => x, StringComparer.Ordinal).ToArray() });
            foreach (var fieldHandle in type.GetFields().OrderBy(context.FieldName, StringComparer.Ordinal))
            {
                var field = reader.GetFieldDefinition(fieldHandle);
                if (field.GetRelativeVirtualAddress() != 0)
                    throw new InvalidDataException("Unreviewed RVA-backed field in guarded type");
                Line(new { kind = "field", name = context.FieldName(fieldHandle), attributes = (int)field.Attributes,
                    signature = field.DecodeSignature(context, (object?)null), offset = field.GetOffset(),
                    constant = context.Constant(field.GetDefaultValue()), custom = context.Custom(field.GetCustomAttributes()),
                    marshal = context.Blob(field.GetMarshallingDescriptor()) });
            }
            foreach (var methodHandle in type.GetMethods().OrderBy(context.MethodName, StringComparer.Ordinal))
            {
                var method = reader.GetMethodDefinition(methodHandle);
                methods++;
                Line(new { kind = "method", name = context.MethodName(methodHandle), attributes = (int)method.Attributes,
                    implementation = (int)method.ImplAttributes, signature = context.Signature(method.DecodeSignature(context, (object?)null)),
                    generic = context.Generics(method.GetGenericParameters()), custom = context.Custom(method.GetCustomAttributes()),
                    parameters = method.GetParameters().Select(x => {
                        var parameter = reader.GetParameter(x);
                        return new { index = parameter.SequenceNumber, name = reader.GetString(parameter.Name),
                            attributes = (int)parameter.Attributes, constant = context.Constant(parameter.GetDefaultValue()),
                            custom = context.Custom(parameter.GetCustomAttributes()), marshal = context.Blob(parameter.GetMarshallingDescriptor()) };
                    }).OrderBy(x => x.index).ToArray(), body = context.Body(method) });
            }
            foreach (var propertyHandle in type.GetProperties().OrderBy(x => reader.GetString(reader.GetPropertyDefinition(x).Name), StringComparer.Ordinal))
            {
                var property = reader.GetPropertyDefinition(propertyHandle);
                var access = property.GetAccessors();
                Line(new { kind = "property", name = reader.GetString(property.Name), attributes = (int)property.Attributes,
                    signature = context.Signature(property.DecodeSignature(context, (object?)null)),
                    getter = context.Entity(access.Getter), setter = context.Entity(access.Setter),
                    others = access.Others.Select(x => context.Entity(x)).OrderBy(x => x, StringComparer.Ordinal).ToArray(),
                    constant = context.Constant(property.GetDefaultValue()), custom = context.Custom(property.GetCustomAttributes()) });
            }
            foreach (var eventHandle in type.GetEvents().OrderBy(x => reader.GetString(reader.GetEventDefinition(x).Name), StringComparer.Ordinal))
            {
                var item = reader.GetEventDefinition(eventHandle);
                var access = item.GetAccessors();
                Line(new { kind = "event", name = reader.GetString(item.Name), attributes = (int)item.Attributes,
                    type = context.Entity(item.Type), adder = context.Entity(access.Adder), remover = context.Entity(access.Remover),
                    raiser = context.Entity(access.Raiser), others = access.Others.Select(x => context.Entity(x)).OrderBy(x => x, StringComparer.Ordinal).ToArray(),
                    custom = context.Custom(item.GetCustomAttributes()) });
            }
            foreach (var implementation in type.GetMethodImplementations()
                .Select(x => reader.GetMethodImplementation(x))
                .OrderBy(x => context.Entity(x.MethodDeclaration), StringComparer.Ordinal))
                Line(new { kind = "method_implementation", declaration = context.Entity(implementation.MethodDeclaration),
                    body = context.Entity(implementation.MethodBody) });
        }
        string canonical = text.ToString();
        return (Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(canonical))), canonical, selected.Length, methods);
    }

    sealed class Context : ISignatureTypeProvider<string, object?>
    {
        readonly MetadataReader reader;
        readonly PEReader pe;
        static readonly Dictionary<ushort, OpCode> Opcodes = typeof(OpCodes).GetFields(BindingFlags.Public | BindingFlags.Static)
            .Where(x => x.FieldType == typeof(OpCode)).Select(x => (OpCode)x.GetValue(null)!)
            .ToDictionary(x => unchecked((ushort)x.Value));

        public Context(MetadataReader reader, PEReader pe) { this.reader = reader; this.pe = pe; }
        public string Blob(BlobHandle handle) => handle.IsNil ? "" : Convert.ToHexString(reader.GetBlobBytes(handle));

        public bool IsGuarded(TypeDefinitionHandle handle)
        {
            var type = reader.GetTypeDefinition(handle);
            return TypeName(handle) == GuardedType || (!type.GetDeclaringType().IsNil && IsGuarded(type.GetDeclaringType()));
        }

        public string TypeName(TypeDefinitionHandle handle)
        {
            var type = reader.GetTypeDefinition(handle);
            var parent = type.GetDeclaringType();
            return parent.IsNil ? Join(reader.GetString(type.Namespace), reader.GetString(type.Name))
                : TypeName(parent) + "+" + reader.GetString(type.Name);
        }

        static string Join(string left, string right) => left.Length == 0 ? right : left + "." + right;

        string TypeReference(TypeReferenceHandle handle)
        {
            var type = reader.GetTypeReference(handle);
            string scope = Entity(type.ResolutionScope);
            return type.ResolutionScope.Kind == HandleKind.TypeReference ? scope + "+" + reader.GetString(type.Name)
                : scope + "::" + Join(reader.GetString(type.Namespace), reader.GetString(type.Name));
        }

        public string FieldName(FieldDefinitionHandle handle)
        {
            var field = reader.GetFieldDefinition(handle);
            return TypeName(field.GetDeclaringType()) + "::" + reader.GetString(field.Name) + ":" + field.DecodeSignature(this, (object?)null);
        }

        public string MethodName(MethodDefinitionHandle handle)
        {
            var method = reader.GetMethodDefinition(handle);
            return TypeName(method.GetDeclaringType()) + "::" + reader.GetString(method.Name)
                + Signature(method.DecodeSignature(this, (object?)null));
        }

        public string Signature(MethodSignature<string> signature) => "[" + signature.Header.RawValue + ";g=" + signature.GenericParameterCount
            + ";required=" + signature.RequiredParameterCount + "](" + string.Join(",", signature.ParameterTypes) + ")->" + signature.ReturnType;

        public string Entity(EntityHandle handle)
        {
            if (handle.IsNil) return "";
            switch (handle.Kind)
            {
                case HandleKind.TypeDefinition: return TypeName((TypeDefinitionHandle)handle);
                case HandleKind.TypeReference: return TypeReference((TypeReferenceHandle)handle);
                case HandleKind.TypeSpecification: return reader.GetTypeSpecification((TypeSpecificationHandle)handle).DecodeSignature(this, (object?)null);
                case HandleKind.MethodDefinition: return MethodName((MethodDefinitionHandle)handle);
                case HandleKind.FieldDefinition: return FieldName((FieldDefinitionHandle)handle);
                case HandleKind.MemberReference:
                    var member = reader.GetMemberReference((MemberReferenceHandle)handle);
                    return Entity(member.Parent) + "::" + reader.GetString(member.Name) + (member.GetKind() == MemberReferenceKind.Field
                        ? ":" + member.DecodeFieldSignature(this, (object?)null) : Signature(member.DecodeMethodSignature(this, (object?)null)));
                case HandleKind.MethodSpecification:
                    var method = reader.GetMethodSpecification((MethodSpecificationHandle)handle);
                    return Entity(method.Method) + "<" + string.Join(",", method.DecodeSignature(this, (object?)null)) + ">";
                case HandleKind.StandaloneSignature:
                    var standalone = reader.GetStandaloneSignature((StandaloneSignatureHandle)handle);
                    return standalone.GetKind() == StandaloneSignatureKind.LocalVariables
                        ? "locals(" + string.Join(",", standalone.DecodeLocalSignature(this, (object?)null)) + ")"
                        : Signature(standalone.DecodeMethodSignature(this, (object?)null));
                case HandleKind.AssemblyReference:
                    var assembly = reader.GetAssemblyReference((AssemblyReferenceHandle)handle);
                    return "assembly(" + reader.GetString(assembly.Name) + "," + assembly.Version + "," + reader.GetString(assembly.Culture)
                        + "," + Blob(assembly.PublicKeyOrToken) + "," + (int)assembly.Flags + ")";
                case HandleKind.ModuleDefinition: return "self";
                case HandleKind.ModuleReference: return "module(" + reader.GetString(reader.GetModuleReference((ModuleReferenceHandle)handle).Name) + ")";
                default: throw new InvalidDataException("Unreviewed metadata token kind: " + handle.Kind);
            }
        }

        public object[] Generics(GenericParameterHandleCollection handles) => handles.Select(handle => {
            var item = reader.GetGenericParameter(handle);
            return new { index = item.Index, name = reader.GetString(item.Name), attributes = (int)item.Attributes,
                constraints = item.GetConstraints().Select(x => Entity(reader.GetGenericParameterConstraint(x).Type))
                    .OrderBy(x => x, StringComparer.Ordinal).ToArray(), custom = Custom(item.GetCustomAttributes()) };
        }).OrderBy(x => x.index).Cast<object>().ToArray();

        public string[] Custom(CustomAttributeHandleCollection handles) => handles.Select(handle => {
            var item = reader.GetCustomAttribute(handle);
            return Entity(item.Constructor) + "=" + Blob(item.Value);
        }).OrderBy(x => x, StringComparer.Ordinal).ToArray();

        public string Constant(ConstantHandle handle)
        {
            if (handle.IsNil) return "";
            var item = reader.GetConstant(handle);
            return item.TypeCode + ":" + Blob(item.Value);
        }

        public object? Body(MethodDefinition method)
        {
            if (method.RelativeVirtualAddress == 0) return null;
            var body = pe.GetMethodBody(method.RelativeVirtualAddress);
            var il = body.GetILBytes() ?? throw new InvalidDataException("No IL bytes");
            var instructions = new List<object>();
            int position = 0;
            while (position < il.Length)
            {
                int offset = position;
                ushort value = il[position++];
                if (value == 0xFE) value = (ushort)(0xFE00 | il[position++]);
                if (!Opcodes.TryGetValue(value, out var opcode)) throw new InvalidDataException("Unrecognized CIL opcode");
                object? operand = null;
                switch (opcode.OperandType)
                {
                    case OperandType.InlineNone: break;
                    case OperandType.ShortInlineI: operand = unchecked((sbyte)il[position++]); break;
                    case OperandType.InlineI: operand = Read32(il, ref position); break;
                    case OperandType.InlineI8:
                        operand = BinaryPrimitives.ReadInt64LittleEndian(il.AsSpan(position, 8)); position += 8; break;
                    case OperandType.ShortInlineR:
                        operand = "float32bits:" + Convert.ToHexString(il.AsSpan(position, 4)); position += 4; break;
                    case OperandType.InlineR:
                        operand = "float64bits:" + Convert.ToHexString(il.AsSpan(position, 8)); position += 8; break;
                    case OperandType.ShortInlineVar: operand = il[position++]; break;
                    case OperandType.InlineVar:
                        operand = BinaryPrimitives.ReadUInt16LittleEndian(il.AsSpan(position, 2)); position += 2; break;
                    case OperandType.ShortInlineBrTarget:
                        int small = unchecked((sbyte)il[position++]); operand = position + small; break;
                    case OperandType.InlineBrTarget:
                        int delta = Read32(il, ref position); operand = position + delta; break;
                    case OperandType.InlineSwitch:
                        int count = Read32(il, ref position);
                        if (count < 0 || count > (il.Length - position) / 4) throw new InvalidDataException("Invalid switch table");
                        int end = position + count * 4;
                        var targets = new int[count];
                        for (int i = 0; i < count; i++) targets[i] = end + Read32(il, ref position);
                        operand = targets; break;
                    case OperandType.InlineString:
                        int stringToken = Read32(il, ref position);
                        if ((unchecked((uint)stringToken) >> 24) != 0x70) throw new InvalidDataException("Invalid user-string token");
                        operand = reader.GetUserString(MetadataTokens.UserStringHandle(stringToken & 0xFFFFFF)); break;
                    case OperandType.InlineField:
                    case OperandType.InlineMethod:
                    case OperandType.InlineType:
                    case OperandType.InlineTok:
                    case OperandType.InlineSig:
                        operand = Entity(MetadataTokens.EntityHandle(Read32(il, ref position))); break;
                    default: throw new InvalidDataException("Unreviewed CIL operand: " + opcode.OperandType);
                }
                instructions.Add(new { offset, opcode = opcode.Name, operand });
            }
            return new { max_stack = body.MaxStack, init_locals = body.LocalVariablesInitialized,
                locals = Entity(body.LocalSignature), instructions,
                exceptions = body.ExceptionRegions.Select(x => new { kind = x.Kind.ToString(),
                    try_offset = x.TryOffset, try_length = x.TryLength, handler_offset = x.HandlerOffset,
                    handler_length = x.HandlerLength, filter_offset = x.Kind == ExceptionRegionKind.Filter ? x.FilterOffset : -1,
                    catch_type = Entity(x.CatchType) }).ToArray() };
        }

        static int Read32(byte[] bytes, ref int position)
        {
            int value = BinaryPrimitives.ReadInt32LittleEndian(bytes.AsSpan(position, 4));
            position += 4;
            return value;
        }

        public string GetArrayType(string element, ArrayShape shape) => element + "[rank=" + shape.Rank
            + ";sizes=" + string.Join(",", shape.Sizes) + ";bounds=" + string.Join(",", shape.LowerBounds) + "]";
        public string GetByReferenceType(string element) => element + "&";
        public string GetFunctionPointerType(MethodSignature<string> signature) => "fnptr" + Signature(signature);
        public string GetGenericInstantiation(string type, System.Collections.Immutable.ImmutableArray<string> arguments) => type + "<" + string.Join(",", arguments) + ">";
        public string GetGenericMethodParameter(object? context, int index) => "!!" + index;
        public string GetGenericTypeParameter(object? context, int index) => "!" + index;
        public string GetModifiedType(string modifier, string type, bool required) => (required ? "modreq(" : "modopt(") + modifier + ")" + type;
        public string GetPinnedType(string element) => "pinned(" + element + ")";
        public string GetPointerType(string element) => element + "*";
        public string GetPrimitiveType(PrimitiveTypeCode code) => "primitive:" + code;
        public string GetSZArrayType(string element) => element + "[]";
        public string GetTypeFromDefinition(MetadataReader source, TypeDefinitionHandle handle, byte kind) => "kind:" + kind + ":" + TypeName(handle);
        public string GetTypeFromReference(MetadataReader source, TypeReferenceHandle handle, byte kind) => "kind:" + kind + ":" + TypeReference(handle);
        public string GetTypeFromSpecification(MetadataReader source, object? context, TypeSpecificationHandle handle, byte kind) => "kind:" + kind + ":" + reader.GetTypeSpecification(handle).DecodeSignature(this, context);
    }
}
