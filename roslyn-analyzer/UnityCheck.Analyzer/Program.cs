using UnityCheck.Analyzer.Endpoints;
using UnityCheck.Analyzer.Services;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddSingleton<RoslynAnalysisService>();
builder.Services.AddProblemDetails();

var app = builder.Build();

app.UseExceptionHandler();

app.MapGet("/health", () => Results.Ok(new { status = "healthy" }));

app.MapPost("/analyze", AnalyzeEndpoints.HandleAnalyze);

app.Run();
