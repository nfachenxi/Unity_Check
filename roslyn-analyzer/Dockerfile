# Stage 1: Build
FROM mcr.microsoft.com/dotnet/sdk:8.0-alpine AS build
ARG BUILD_CONFIGURATION=Release
ARG RUNTIME=linux-musl-x64
WORKDIR /src

COPY ["UnityCheck.Analyzer/UnityCheck.Analyzer.csproj", "UnityCheck.Analyzer/"]
RUN dotnet restore "UnityCheck.Analyzer/UnityCheck.Analyzer.csproj" -r "$RUNTIME"

COPY . .
RUN dotnet publish "UnityCheck.Analyzer/UnityCheck.Analyzer.csproj" \
    -c "$BUILD_CONFIGURATION" \
    -r "$RUNTIME" \
    --self-contained false \
    -o /app/publish \
    /p:UseAppHost=false \
    /p:PublishReadyToRun=true

# Stage 2: Runtime
FROM mcr.microsoft.com/dotnet/aspnet:8.0-alpine

ENV DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=true
ENV ASPNETCORE_URLS=http://+:8080

WORKDIR /app
USER app
EXPOSE 8080

COPY --from=build /app/publish .

ENTRYPOINT ["dotnet", "UnityCheck.Analyzer.dll"]
