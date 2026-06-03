using Microsoft.AspNetCore.Http.HttpResults;
using UnityCheck.Analyzer.Models;
using UnityCheck.Analyzer.Services;

namespace UnityCheck.Analyzer.Endpoints;

public static class AnalyzeEndpoints
{
    /// <summary>
    /// POST /analyze — accepts a list of files (path + content) and returns
    /// structured Roslyn diagnostic results.
    /// </summary>
    public static async Task<Results<Ok<AnalyzeResponse>, BadRequest<object>>> HandleAnalyze(
        AnalyzeRequest request,
        RoslynAnalysisService service)
    {
        if (request.Files is null || request.Files.Count == 0)
            return TypedResults.BadRequest<object>(new { error = "At least one file is required in the 'files' array." });

        var diagnostics = await service.AnalyzeAsync(request.Files);

        var response = new AnalyzeResponse(
            Success: true,
            TotalDiagnostics: diagnostics.Count,
            Errors: diagnostics.Count(d => d.Severity == "Error"),
            Warnings: diagnostics.Count(d => d.Severity == "Warning"),
            Infos: diagnostics.Count(d => d.Severity == "Info"),
            Diagnostics: diagnostics
        );

        return TypedResults.Ok(response);
    }
}
