// Offline operation producer only. Never initialize a link or call Loader.
// The Python caller copies these sources and sealed PGs into a NEW directory.
open System
open System.IO
open System.Collections.Generic
open System.Reflection
open System.Text.Json
open System.Text.Json.Nodes
open FluxSDK.Build.Incremental
open FluxSDK.Incremental.StepCE
open FluxSDK.ResoniteLink

[<EntryPoint>]
let main argv =
    if argv.Length <> 5 then
        failwith "Expected project directory, module, native DLL directory, bindings, NEW output"
    let project, moduleName, library, bindingsPath, output =
        Path.GetFullPath argv.[0], argv.[1], argv.[2], argv.[3], Path.GetFullPath argv.[4]
    if not (Set.contains moduleName (set ["DriveGateProbe"; "ColorDrive"; "BoolDrive"])) then
        failwith "Not a Drivegate module"
    if File.Exists output || Directory.Exists output then failwith "Output already exists"
    use bindings = JsonDocument.Parse(File.ReadAllText bindingsPath)
    let readMap (name: string) =
        let result = Dictionary<string,string>()
        for item in bindings.RootElement.GetProperty(name).EnumerateObject() do
            let target = item.Value.GetString()
            if target <> "private:" + item.Name then failwith "Non-symbolic port binding"
            result.Add(item.Name, target)
        result
    let inputs, outputs = readMap "inputs", readMap "outputs"
    let store = Build.initializeStoreWith [|library|]
    let config = Build.defaultConfig (Build.loadManifest project)
    store.SetInput(BuildConfigKey(), config)
    // No Link.initialize, linkInterface, Query, Loader, URL or native operation.
    let ir = Step.runStepAsync (Build.moduleCompiler project moduleName) store
             |> Async.AwaitTask |> Async.RunSynchronously
    let operations = Link.batchAddProtoGraphNodes(config, "native-layout-parent", ir, inputs, outputs)
    let opts = JsonSerializerOptions(WriteIndented=true)
    let rows = JsonArray()
    for op in operations do
        let kind = op.GetType().Name
        if kind <> "AddSlot" && kind <> "AddComponent" then failwith $"Unexpected operation: {kind}"
        let row = JsonSerializer.SerializeToNode(op, op.GetType(), opts).AsObject()
        row.["operation"] <- JsonValue.Create(kind)
        rows.Add(row)
    let version = typeof<BuildConfigKey>.Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()
    let doc = JsonObject()
    doc.["format"] <- JsonValue.Create("emission-native-layout-v1")
    doc.["module"] <- JsonValue.Create(ir.ModuleName)
    doc.["sdkVersion"] <- JsonValue.Create(version.InformationalVersion)
    doc.["operationCount"] <- JsonValue.Create(operations.Count)
    doc.["operations"] <- rows
    use stream = new FileStream(output, FileMode.CreateNew)
    use writer = new Utf8JsonWriter(stream, JsonWriterOptions(Indented=true))
    doc.WriteTo(writer, opts)
    Console.Error.WriteLine($"OFFLINE operations={operations.Count}; no link initialized")
    0
