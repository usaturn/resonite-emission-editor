using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using Elements.Core;
using FrooxEngine;
using ResoniteModLoader;

namespace ResoniteEmissionAutoRegister;

/// <summary>
/// Fills the empty rows of the Issue #6 registration panel with the
/// EmissionColor fields of the materials used by the avatar dropped into the
/// panel. This is a deliberate mod-based extension: vanilla ProtoFlux cannot
/// enumerate components, so the panel cannot find materials by itself.
///
/// Safety boundaries:
/// - Only panels whose root name starts with ResoLoop_Test_EmissionEditor_Registrable_.
/// - Only writes panel-owned rp-N-dropped ReferenceField and rp-N-label fields.
/// - Only fills empty rows; it never overwrites a row the user registered.
/// - Never writes avatar, material, or other world values.
/// </summary>
public sealed class EmissionAutoRegister : ResoniteMod
{
    const string PanelPrefix = "ResoLoop_Test_EmissionEditor_Registrable_";

    public override string Name => "EmissionAutoRegister";
    public override string Author => "usaturn";
    public override string Version => "0.1.0";
    public override string Link => "https://github.com/usaturn/resonite-emission-editor";

    static volatile bool running;
    static Thread? worker;
    static readonly HashSet<(RefID Panel, RefID Avatar)> processed = new();
    static readonly object processedLock = new();

    public override void OnEngineInit()
    {
        Engine.Current.OnReady += Start;
        Engine.Current.OnShutdown += Stop;
        Msg("loaded; waiting for engine ready");
    }

    static void Start()
    {
        if (running) return;
        running = true;
        worker = new Thread(Loop)
        {
            IsBackground = true,
            Name = "EmissionAutoRegister",
        };
        worker.Start();
        Msg("started");
    }

    static void Stop()
    {
        running = false;
    }

    static void Loop()
    {
        while (running)
        {
            try
            {
                var manager = Engine.Current?.WorldManager;
                if (manager != null)
                {
                    foreach (var world in manager.Worlds.ToArray())
                    {
                        if (world == null || world.IsDestroyed) continue;
                        try
                        {
                            world.RunSynchronously(() => ScanWorld(world));
                        }
                        catch (Exception error)
                        {
                            Error("world scan failed: " + error);
                        }
                    }
                }
            }
            catch (Exception error)
            {
                Error("loop failed: " + error);
            }
            Thread.Sleep(1000);
        }
    }

    static void ScanWorld(World world)
    {
        var rootSlot = world.RootSlot;
        if (rootSlot == null) return;

        foreach (var panel in rootSlot.Children)
        {
            if (panel == null || panel.IsDestroyed) continue;
            if (!panel.Name.StartsWith(PanelPrefix, StringComparison.Ordinal)) continue;

            var registry = panel.FindChild("registry-root");
            if (registry == null) continue;

            var ready = registry.FindChild("registry-ready-slot")
                ?.GetComponentInChildren<ValueField<bool>>();
            if (ready == null || !ready.Value.Value) continue;

            var avatarField = registry.FindChild("ui-pending-root-slot")
                ?.GetComponentInChildren<ReferenceField<Slot>>();
            var avatar = avatarField?.Reference.Target;
            if (avatar == null || avatar.IsDestroyed) continue;

            var key = (panel.ReferenceID, avatar.ReferenceID);
            lock (processedLock)
            {
                if (processed.Contains(key)) continue;
            }

            var rows = CollectRows(registry);
            if (rows.Count == 0) continue;

            var materials = CollectMaterials(avatar);
            var bound = 0;
            var rowIndex = 0;
            foreach (var material in materials)
            {
                while (rowIndex < rows.Count && rows[rowIndex].Field.Reference.Target != null)
                    rowIndex++;
                if (rowIndex >= rows.Count) break;

                var row = rows[rowIndex];
                row.Field.Reference.Target = material.EmissionColor;
                if (row.Label != null)
                    row.Label.Value.Value = $"{rowIndex + 1:00} {material.Slot.Name}";
                Msg($"bound {panel.Name} row {rowIndex}: {material.Slot.Name}");
                bound++;
                rowIndex++;
            }

            lock (processedLock)
            {
                processed.Add(key);
            }
            Msg($"scanned {panel.Name}: {materials.Count} materials, {bound} bound");
        }
    }

    static List<(ReferenceField<IValue<colorX>> Field, ValueField<string>? Label)> CollectRows(Slot registry)
    {
        var rows = new List<(ReferenceField<IValue<colorX>>, ValueField<string>?)>();
        for (var index = 0; ; index++)
        {
            var row = registry.FindChild($"rp-{index}");
            if (row == null) break;
            var field = row.FindChild($"rp-{index}-dropped-slot")
                ?.GetComponentInChildren<ReferenceField<IValue<colorX>>>();
            if (field == null) break;
            var label = row.FindChild($"rp-{index}-label-slot")
                ?.GetComponentInChildren<ValueField<string>>();
            rows.Add((field, label));
        }
        return rows;
    }

    static List<XiexeToonMaterial> CollectMaterials(Slot avatar)
    {
        var result = new List<XiexeToonMaterial>();
        var seen = new HashSet<RefID>();

        void AddProvider(IAssetProvider<Material> provider)
        {
            if (provider is not XiexeToonMaterial material) return;
            if (material.EmissionColor == null) return;
            if (seen.Add(material.ReferenceID)) result.Add(material);
        }

        foreach (var renderer in avatar.GetComponentsInChildren<MeshRenderer>())
        {
            foreach (var provider in renderer.Materials) AddProvider(provider);
        }
        foreach (var renderer in avatar.GetComponentsInChildren<SkinnedMeshRenderer>())
        {
            foreach (var provider in renderer.Materials) AddProvider(provider);
        }
        return result;
    }
}
