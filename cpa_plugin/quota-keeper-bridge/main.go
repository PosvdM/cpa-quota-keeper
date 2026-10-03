package main

/*
#include <stdint.h>
#include <stdlib.h>

typedef struct {
    void* ptr;
    size_t len;
} cliproxy_buffer;

typedef int (*cliproxy_host_call_fn)(void*, const char*, const uint8_t*, size_t, cliproxy_buffer*);
typedef void (*cliproxy_host_free_fn)(void*, size_t);

typedef struct {
    uint32_t abi_version;
    void* host_ctx;
    cliproxy_host_call_fn call;
    cliproxy_host_free_fn free_buffer;
} cliproxy_host_api;

typedef int (*cliproxy_plugin_call_fn)(char*, uint8_t*, size_t, cliproxy_buffer*);
typedef void (*cliproxy_plugin_free_fn)(void*, size_t);
typedef void (*cliproxy_plugin_shutdown_fn)(void);

typedef struct {
    uint32_t abi_version;
    cliproxy_plugin_call_fn call;
    cliproxy_plugin_free_fn free_buffer;
    cliproxy_plugin_shutdown_fn shutdown;
} cliproxy_plugin_api;

extern int cliproxyPluginCall(char*, uint8_t*, size_t, cliproxy_buffer*);
extern void cliproxyPluginFree(void*, size_t);
extern void cliproxyPluginShutdown(void);

static const cliproxy_host_api* stored_host;
static void store_host_api(const cliproxy_host_api* host) { stored_host = host; }
static int call_host_api(const char* method, const uint8_t* request, size_t request_len, cliproxy_buffer* response) {
    if (stored_host == NULL || stored_host->call == NULL) return 1;
    return stored_host->call(stored_host->host_ctx, method, request, request_len, response);
}
static void free_host_buffer(void* ptr, size_t len) {
    if (stored_host != NULL && stored_host->free_buffer != NULL && ptr != NULL) {
        stored_host->free_buffer(ptr, len);
    }
}
*/
import "C"

import (
    "encoding/json"
    "fmt"
    "net/http"
    "strings"
    "unsafe"

    "github.com/router-for-me/CLIProxyAPI/v8/sdk/pluginabi"
    "github.com/router-for-me/CLIProxyAPI/v8/sdk/pluginapi"
)

const (
    pluginName = "quota-keeper-bridge"
    routePath  = "/quota-keeper/ignite"
)

type envelope struct {
    OK     bool            `json:"ok"`
    Result json.RawMessage `json:"result,omitempty"`
    Error  *envelopeError  `json:"error,omitempty"`
}

type envelopeError struct {
    Code    string `json:"code"`
    Message string `json:"message"`
}

type registration struct {
    SchemaVersion uint32 `json:"schema_version"`
    Metadata struct {
        Name string `json:"Name"`
        Version string `json:"Version"`
        Author string `json:"Author"`
        GitHubRepository string `json:"GitHubRepository"`
        ConfigFields []any `json:"ConfigFields"`
    } `json:"metadata"`
    Capabilities struct {
        ManagementAPI bool `json:"management_api"`
    } `json:"capabilities"`
}

type managementRegistration struct {
    Routes []managementRoute `json:"Routes"`
}

type managementRoute struct {
    Method string `json:"Method"`
    Path string `json:"Path"`
    Description string `json:"Description"`
}

type managementRequest struct {
    Method string `json:"Method"`
    Path string `json:"Path"`
    Body []byte `json:"Body"`
    HostCallbackID string `json:"host_callback_id,omitempty"`
}

type igniteRequest struct {
    AuthIndex string `json:"auth_index"`
    Model string `json:"model"`
    EntryProtocol string `json:"entry_protocol"`
    ExitProtocol string `json:"exit_protocol"`
    Body json.RawMessage `json:"body"`
}

type hostModelExecutionRequest struct {
    pluginapi.HostModelExecutionRequest
    HostCallbackID string `json:"host_callback_id,omitempty"`
}

type igniteResponse struct {
    OK bool `json:"ok"`
    Provider string `json:"provider"`
    Model string `json:"model"`
    AuthIndex string `json:"auth_index"`
}

func main() {}

//export cliproxy_plugin_init
func cliproxy_plugin_init(host *C.cliproxy_host_api, plugin *C.cliproxy_plugin_api) C.int {
    if plugin == nil { return 1 }
    C.store_host_api(host)
    plugin.abi_version = C.uint32_t(pluginabi.ABIVersion)
    plugin.call = C.cliproxy_plugin_call_fn(C.cliproxyPluginCall)
    plugin.free_buffer = C.cliproxy_plugin_free_fn(C.cliproxyPluginFree)
    plugin.shutdown = C.cliproxy_plugin_shutdown_fn(C.cliproxyPluginShutdown)
    return 0
}

//export cliproxyPluginCall
func cliproxyPluginCall(method *C.char, request *C.uint8_t, requestLen C.size_t, response *C.cliproxy_buffer) C.int {
    if response != nil { response.ptr = nil; response.len = 0 }
    if method == nil {
        writeResponse(response, errorEnvelope("invalid_method", "method is required"))
        return 1
    }
    var raw []byte
    if request != nil && requestLen > 0 {
        raw = C.GoBytes(unsafe.Pointer(request), C.int(requestLen))
    }
    out, err := handleMethod(C.GoString(method), raw)
    if err != nil {
        writeResponse(response, errorEnvelope("plugin_error", err.Error()))
        return 1
    }
    writeResponse(response, out)
    return 0
}

//export cliproxyPluginFree
func cliproxyPluginFree(ptr unsafe.Pointer, len C.size_t) { if ptr != nil { C.free(ptr) }; _ = len }

//export cliproxyPluginShutdown
func cliproxyPluginShutdown() {}

func handleMethod(method string, raw []byte) ([]byte, error) {
    switch method {
    case pluginabi.MethodPluginRegister, pluginabi.MethodPluginReconfigure:
        var reg registration
        reg.SchemaVersion = pluginabi.SchemaVersion
        reg.Metadata.Name = pluginName
        reg.Metadata.Version = "0.1.0"
        reg.Metadata.Author = "PosvdM"
        reg.Metadata.GitHubRepository = "https://github.com/PosvdM/cpa-quota-keeper"
        reg.Metadata.ConfigFields = []any{}
        reg.Capabilities.ManagementAPI = true
        return okEnvelope(reg)
    case pluginabi.MethodManagementRegister:
        return okEnvelope(managementRegistration{Routes: []managementRoute{{
            Method: http.MethodPost,
            Path: routePath,
            Description: "Run one quota ignition request through CPA's normal model executor with an exact auth binding.",
        }}})
    case pluginabi.MethodManagementHandle:
        return handleManagement(raw)
    default:
        return errorEnvelope("unknown_method", "unknown method: "+method), nil
    }
}

func handleManagement(raw []byte) ([]byte, error) {
    var req managementRequest
    if err := json.Unmarshal(raw, &req); err != nil {
        return nil, fmt.Errorf("decode management request: %w", err)
    }
    var in igniteRequest
    if err := json.Unmarshal(req.Body, &in); err != nil {
        return okEnvelope(pluginapi.ManagementResponse{StatusCode: http.StatusBadRequest, Headers: jsonHeaders(), Body: jsonBody(map[string]any{"error":"invalid JSON body"})})
    }
    in.AuthIndex = strings.TrimSpace(in.AuthIndex)
    in.Model = strings.TrimSpace(in.Model)
    in.EntryProtocol = strings.TrimSpace(in.EntryProtocol)
    in.ExitProtocol = strings.TrimSpace(in.ExitProtocol)
    if in.AuthIndex == "" || in.Model == "" || in.EntryProtocol == "" || len(in.Body) == 0 {
        return okEnvelope(pluginapi.ManagementResponse{StatusCode: http.StatusBadRequest, Headers: jsonHeaders(), Body: jsonBody(map[string]any{"error":"auth_index, model, entry_protocol and body are required"})})
    }
    if in.ExitProtocol == "" { in.ExitProtocol = in.EntryProtocol }

    runtime, err := authRuntime(in.AuthIndex)
    if err != nil {
        return okEnvelope(pluginapi.ManagementResponse{StatusCode: http.StatusBadRequest, Headers: jsonHeaders(), Body: jsonBody(map[string]any{"error":err.Error()})})
    }
    auth := runtime.Auth
    if auth.ID == "" || auth.Disabled || auth.Unavailable {
        return okEnvelope(pluginapi.ManagementResponse{StatusCode: http.StatusConflict, Headers: jsonHeaders(), Body: jsonBody(map[string]any{"error":"credential is unavailable"})})
    }

    result, err := callHost(pluginabi.MethodHostModelExecute, hostModelExecutionRequest{
        HostModelExecutionRequest: pluginapi.HostModelExecutionRequest{
            EntryProtocol: in.EntryProtocol,
            ExitProtocol: in.ExitProtocol,
            Model: in.Model,
            Stream: false,
            Body: append([]byte(nil), in.Body...),
            ForcedProvider: auth.Provider,
            AuthID: auth.ID,
        },
        HostCallbackID: strings.TrimSpace(req.HostCallbackID),
    })
    if err != nil {
        return okEnvelope(pluginapi.ManagementResponse{StatusCode: http.StatusBadGateway, Headers: jsonHeaders(), Body: jsonBody(map[string]any{"error":err.Error()})})
    }
    var modelResp pluginapi.HostModelExecutionResponse
    if err := json.Unmarshal(result, &modelResp); err != nil {
        return okEnvelope(pluginapi.ManagementResponse{StatusCode: http.StatusBadGateway, Headers: jsonHeaders(), Body: jsonBody(map[string]any{"error":"invalid host model response"})})
    }
    if modelResp.StatusCode < 200 || modelResp.StatusCode >= 300 {
        return okEnvelope(pluginapi.ManagementResponse{StatusCode: modelResp.StatusCode, Headers: jsonHeaders(), Body: jsonBody(map[string]any{"error":"model execution failed"})})
    }
    return okEnvelope(pluginapi.ManagementResponse{StatusCode: http.StatusOK, Headers: jsonHeaders(), Body: jsonBody(igniteResponse{OK:true, Provider:auth.Provider, Model:in.Model, AuthIndex:in.AuthIndex})})
}

func authRuntime(authIndex string) (pluginapi.HostAuthGetRuntimeResponse, error) {
    raw, err := callHost(pluginabi.MethodHostAuthGetRuntime, pluginapi.HostAuthGetRequest{AuthIndex: authIndex})
    if err != nil { return pluginapi.HostAuthGetRuntimeResponse{}, err }
    var resp pluginapi.HostAuthGetRuntimeResponse
    if err := json.Unmarshal(raw, &resp); err != nil { return resp, fmt.Errorf("decode auth runtime: %w", err) }
    return resp, nil
}

func callHost(method string, payload any) (json.RawMessage, error) {
    rawPayload, err := json.Marshal(payload)
    if err != nil { return nil, err }
    cMethod := C.CString(method)
    defer C.free(unsafe.Pointer(cMethod))
    var response C.cliproxy_buffer
    var requestPtr *C.uint8_t
    if len(rawPayload) > 0 {
        cPayload := C.CBytes(rawPayload)
        if cPayload == nil { return nil, fmt.Errorf("allocate host payload") }
        defer C.free(cPayload)
        requestPtr = (*C.uint8_t)(cPayload)
    }
    code := C.call_host_api(cMethod, requestPtr, C.size_t(len(rawPayload)), &response)
    var rawResponse []byte
    if response.ptr != nil && response.len > 0 { rawResponse = C.GoBytes(response.ptr, C.int(response.len)) }
    if response.ptr != nil { C.free_host_buffer(response.ptr, response.len) }
    if len(rawResponse) == 0 { return nil, fmt.Errorf("host callback %s returned no response", method) }
    var env envelope
    if err := json.Unmarshal(rawResponse, &env); err != nil { return nil, fmt.Errorf("decode host envelope: %w", err) }
    if !env.OK {
        if env.Error != nil { return nil, fmt.Errorf("%s: %s", env.Error.Code, env.Error.Message) }
        return nil, fmt.Errorf("host callback %s failed", method)
    }
    if code != 0 { return nil, fmt.Errorf("host callback %s returned code=%d", method, int(code)) }
    return append(json.RawMessage(nil), env.Result...), nil
}

func jsonHeaders() http.Header { return http.Header{"Content-Type": []string{"application/json"}} }
func jsonBody(v any) []byte { raw, _ := json.Marshal(v); return raw }
func okEnvelope(v any) ([]byte, error) { raw, err := json.Marshal(v); if err != nil { return nil, err }; return json.Marshal(envelope{OK:true, Result:raw}) }
func errorEnvelope(code, message string) []byte { raw, _ := json.Marshal(envelope{OK:false, Error:&envelopeError{Code:code, Message:message}}); return raw }
func writeResponse(response *C.cliproxy_buffer, raw []byte) { if response == nil || len(raw)==0 { return }; ptr:=C.CBytes(raw); if ptr==nil { return }; response.ptr=ptr; response.len=C.size_t(len(raw)) }
