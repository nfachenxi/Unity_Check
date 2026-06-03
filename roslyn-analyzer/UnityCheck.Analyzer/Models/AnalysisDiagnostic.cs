namespace UnityCheck.Analyzer.Models;

public class AnalysisDiagnostic
{
    public string Id { get; set; } = string.Empty;
    public string Severity { get; set; } = string.Empty;
    public string Category { get; set; } = string.Empty;
    public string Title { get; set; } = string.Empty;
    public string? Description { get; set; }
    public string Message { get; set; } = string.Empty;
    public string? HelpLink { get; set; }
    public bool IsSuppressed { get; set; }
    public string FilePath { get; set; } = string.Empty;
    public int StartLine { get; set; }
    public int StartColumn { get; set; }
    public int EndLine { get; set; }
    public int EndColumn { get; set; }
    public string? Snippet { get; set; }
}
