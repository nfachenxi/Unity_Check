using System.Collections.Immutable;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.Diagnostics;
using Microsoft.CodeAnalysis.Text;
using UnityCheck.Analyzer.AnalyzerLoading;
using UnityCheck.Analyzer.Models;

namespace UnityCheck.Analyzer.Services;

/// <summary>
/// Accepts source-code strings, runs all loaded Roslyn analyzers, and
/// returns structured diagnostics.
/// </summary>
public class RoslynAnalysisService
{
    private readonly ImmutableArray<DiagnosticAnalyzer> _analyzers;
    private readonly List<MetadataReference> _references;
    private readonly ILogger<RoslynAnalysisService> _logger;

    public RoslynAnalysisService(ILogger<RoslynAnalysisService> logger)
    {
        _logger = logger;
        _analyzers = AnalyzerLoader.LoadAllAvailableAnalyzers();
        _references = BuildMetadataReferences();
        _logger.LogInformation("RoslynAnalysisService initialized with {Count} analyzers and {RefCount} metadata references",
            _analyzers.Length, _references.Count);
    }

    /// <summary>
    /// Analyze a list of source files and return sorted diagnostics per file.
    /// </summary>
    public async Task<List<AnalysisDiagnostic>> AnalyzeAsync(List<FileInput> files)
    {
        var allDiagnostics = new List<AnalysisDiagnostic>();

        foreach (var file in files)
        {
            if (string.IsNullOrWhiteSpace(file.Content))
                continue;

            var fileDiagnostics = await AnalyzeSingleFileAsync(file);
            allDiagnostics.AddRange(fileDiagnostics);
        }

        return allDiagnostics
            .OrderBy(d => d.Severity)      // Error > Warning > Info
            .ThenBy(d => d.Id)
            .ToList();
    }

    private async Task<List<AnalysisDiagnostic>> AnalyzeSingleFileAsync(FileInput file)
    {
        var parseOptions = new CSharpParseOptions(LanguageVersion.Latest);
        var syntaxTree = CSharpSyntaxTree.ParseText(file.Content, parseOptions, file.Path);

        var compilation = CSharpCompilation.Create(
            $"Analysis_{Guid.NewGuid():N}")
            .WithOptions(new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary))
            .AddReferences(_references)
            .AddSyntaxTrees(syntaxTree);

        // If no analyzers loaded, still return compiler diagnostics.
        if (_analyzers.IsEmpty)
        {
            var compilerDiagnostics = compilation.GetDiagnostics()
                .Where(d => d.Severity is DiagnosticSeverity.Error or DiagnosticSeverity.Warning or DiagnosticSeverity.Info)
                .Select(d => MapDiagnostic(d, file.Path))
                .ToList();
            return compilerDiagnostics;
        }

        try
        {
            var compilationWithAnalyzers = compilation.WithAnalyzers(_analyzers);
            var diagnostics = await compilationWithAnalyzers.GetAllDiagnosticsAsync();
            return diagnostics
                .Where(d => d.Severity is DiagnosticSeverity.Error or DiagnosticSeverity.Warning or DiagnosticSeverity.Info)
                .Select(d => MapDiagnostic(d, file.Path))
                .ToList();
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Analyzer execution failed for {FilePath}, falling back to compiler diagnostics", file.Path);
            var compilerDiagnostics = compilation.GetDiagnostics()
                .Where(d => d.Severity is DiagnosticSeverity.Error or DiagnosticSeverity.Warning or DiagnosticSeverity.Info)
                .Select(d => MapDiagnostic(d, file.Path))
                .ToList();
            return compilerDiagnostics;
        }
    }

    private static AnalysisDiagnostic MapDiagnostic(Diagnostic diagnostic, string filePath)
    {
        var descriptor = diagnostic.Descriptor;
        var location = diagnostic.Location;
        LinePositionSpan lineSpan;

        if (location.IsInSource)
        {
            lineSpan = location.GetLineSpan().Span;
        }
        else
        {
            lineSpan = new LinePositionSpan(
                new LinePosition(0, 0),
                new LinePosition(0, 0));
        }

        return new AnalysisDiagnostic
        {
            Id = diagnostic.Id,
            Severity = diagnostic.Severity.ToString(),
            Category = descriptor.Category ?? "",
            Title = descriptor.Title.ToString(),
            Description = descriptor.Description?.ToString(),
            Message = diagnostic.GetMessage(),
            HelpLink = descriptor.HelpLinkUri,
            IsSuppressed = diagnostic.IsSuppressed,
            FilePath = filePath,
            StartLine = lineSpan.Start.Line + 1,
            StartColumn = lineSpan.Start.Character + 1,
            EndLine = lineSpan.End.Line + 1,
            EndColumn = lineSpan.End.Character + 1,
            Snippet = location.IsInSource
                ? location.SourceTree?.GetText().ToString(location.SourceSpan)
                : null,
        };
    }

    /// <summary>
    /// Build a broad set of BCL references so semantic analysis can resolve
    /// types like object, IEnumerable, System.Console, etc.
    /// </summary>
    private static List<MetadataReference> BuildMetadataReferences()
    {
        var references = new List<MetadataReference>();

        // Always reference the core runtime assembly.
        var coreAssembly = typeof(object).Assembly;
        references.Add(MetadataReference.CreateFromFile(coreAssembly.Location));

        // Add all loaded assemblies to maximise the chance of resolving
        // Unity-adjacent or BCL types.
        var addedPaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            coreAssembly.Location,
        };

        foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
        {
            if (assembly.IsDynamic || string.IsNullOrEmpty(assembly.Location))
                continue;
            if (!addedPaths.Add(assembly.Location))
                continue;
            try
            {
                references.Add(MetadataReference.CreateFromFile(assembly.Location));
            }
            catch
            {
                // Not every assembly can be used as a metadata reference
                // (e.g. native images); skip silently.
            }
        }

        return references;
    }
}
