package com.lyrpay.app;

import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    // Google blocks sign-in inside WebViews, so the site runs the Cognito Hosted UI
    // in a Custom Tab that redirects to com.lyrpay.app://auth/callback. Bringing this
    // singleTask activity forward closes the tab; the WebView then finishes the
    // code exchange on the real callback page, where the PKCE verifier is stored.
    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        handleAuthRedirect(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        handleAuthRedirect(intent);
    }

    private void handleAuthRedirect(Intent intent) {
        Uri data = intent == null ? null : intent.getData();
        if (data == null || !"com.lyrpay.app".equals(data.getScheme()) || !"auth".equals(data.getHost())) return;
        String query = data.getEncodedQuery();
        String url = "https://lyrpay.com/auth/callback" + (query == null ? "" : "?" + query);
        bridge.getWebView().post(() -> bridge.getWebView().loadUrl(url));
    }
}
