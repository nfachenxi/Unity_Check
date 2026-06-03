namespace UnityCheck.Analyzer.Models;

public record AnalyzeResponse(
    bool Success,
    int TotalDiagnostics,
    int Errors,
    int Warnings,
    int Infos,
    List<AnalysisDiagnostic> Diagnostics
);
