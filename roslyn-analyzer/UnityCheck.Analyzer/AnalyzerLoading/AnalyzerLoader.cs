using System.Collections.Immutable;
using System.Reflection;
using Microsoft.CodeAnalysis.Diagnostics;

namespace UnityCheck.Analyzer.AnalyzerLoading;

/// <summary>
/// Loads DiagnosticAnalyzer instances at runtime by scanning the publish
/// output directory for analyzer DLLs bundled with the application.
/// </summary>
public static class AnalyzerLoader
{
    public static ImmutableArray<DiagnosticAnalyzer> LoadAllAvailableAnalyzers()
    {
        var analyzers = new List<DiagnosticAnalyzer>();

        // Scan the application's base directory for DLLs containing analyzers.
        var baseDir = AppContext.BaseDirectory;
        if (!Directory.Exists(baseDir))
            return ImmutableArray<DiagnosticAnalyzer>.Empty;

        foreach (var dllPath in Directory.EnumerateFiles(baseDir, "*.dll"))
        {
            Assembly assembly;
            try
            {
                var assemblyName = AssemblyName.GetAssemblyName(dllPath);
                assembly = Assembly.Load(assemblyName);
            }
            catch
            {
                // Not a managed assembly or cannot load — skip.
                continue;
            }

            Type[] types;
            try
            {
                types = assembly.GetTypes();
            }
            catch (ReflectionTypeLoadException ex)
            {
                types = ex.Types.Where(t => t is not null).ToArray()!;
            }

            foreach (var type in types)
            {
                if (type.IsAbstract || type.IsInterface)
                    continue;

                var attr = type.GetCustomAttribute<DiagnosticAnalyzerAttribute>();
                if (attr is null)
                    continue;

                if (Activator.CreateInstance(type) is DiagnosticAnalyzer instance)
                {
                    analyzers.Add(instance);
                }
            }
        }

        return ImmutableArray.CreateRange(analyzers);
    }

    /// <summary>
    /// Returns how many analyzer instances were loaded (for health/logging).
    /// </summary>
    public static int GetLoadedAnalyzerCount() => LoadAllAvailableAnalyzers().Length;
}
