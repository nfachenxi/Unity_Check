namespace UnityCheck.Analyzer.Models;

public record AnalyzeRequest(
    List<FileInput> Files
);

public record FileInput(
    string Path,
    string Content
);
