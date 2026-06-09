#import <Foundation/Foundation.h>
#import <Capacitor/Capacitor.h>

// Registers the Swift CmfScannerPlugin with Capacitor under the JS name "CmfScanner".
// Capacitor auto-discovers app-embedded plugins declared with this macro.
CAP_PLUGIN(CmfScannerPlugin, "CmfScanner",
    CAP_PLUGIN_METHOD(isAvailable, CAPPluginReturnPromise);
    CAP_PLUGIN_METHOD(scan, CAPPluginReturnPromise);
)
