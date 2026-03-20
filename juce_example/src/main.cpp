#include <juce_core/juce_core.h>
#include <juce_events/juce_events.h>

using namespace juce;

static int httpGet(const String& url, String& outBody)
{
    URL u(url);
    StringPairArray headers;
    headers.set("User-Agent", "edmg_juce_client/0.1");

    std::cout << "[DEBUG] Attempting to connect to: " << url << "\n";
    std::cout.flush();

    std::unique_ptr<InputStream> stream(u.createInputStream(URL::InputStreamOptions(URL::ParameterHandling::inAddress)
                                                                .withExtraHeaders(headers.getDescription())
                                                                .withConnectionTimeoutMs(8000)));
    if (!stream)
    {
        std::cout << "[ERROR] Failed to create input stream - connection failed or timeout\n";
        std::cout.flush();
        return 0;
    }

    outBody = stream->readEntireStreamAsString();
    std::cout << "[DEBUG] Response received, body length: " << outBody.length() << "\n";
    std::cout.flush();
    return 200;
}

int main (int argc, char* argv[])
{
    ConsoleApplication app;

    String baseUrl = "http://127.0.0.1:5173";
    if (argc >= 2)
        baseUrl = argv[1];

    std::cout << "[INFO] Starting edmg_juce_client\n";
    std::cout << "[INFO] Backend URL: " << baseUrl << "\n";
    std::cout << "[INFO] Performing health check...\n";
    std::cout.flush();

    String body;
    auto status = httpGet(baseUrl + "/health", body);

    std::cout << "\n[RESULT] GET " << (baseUrl + "/health") << " -> " << status << "\n";
    std::cout << "[RESPONSE] " << body << "\n";
    std::cout << "[EXIT] Returning: " << (status == 200 ? 0 : 2) << "\n";
    std::cout.flush();

    return status == 200 ? 0 : 2;
}
