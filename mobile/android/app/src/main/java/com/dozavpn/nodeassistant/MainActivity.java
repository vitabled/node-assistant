package com.dozavpn.nodeassistant;

import android.os.Bundle;
import android.view.ViewGroup;
import android.webkit.WebView;
import androidx.activity.EdgeToEdge;
import androidx.activity.OnBackPressedCallback;
import androidx.activity.SystemBarStyle;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        // Одинаковое поведение на всех версиях: окно всегда edge-to-edge, бары тёмные (M3 surface), иконки светлые.
        EdgeToEdge.enable(this, SystemBarStyle.dark(0xFF141218), SystemBarStyle.dark(0xFF141218));
        super.onCreate(savedInstanceState);
        // Android 15+ (targetSdk 35) рисует окно edge-to-edge: без отступов WebView залезает под
        // шторку уведомлений, вырез камеры и нижнюю навигацию, а клавиатура перекрывает поля ввода.
        // Отодвигаем WebView на системные отступы; инсеты поглощаем, чтобы CSS env(safe-area-*) не удвоил их.
        WebView web = getBridge().getWebView();
        ViewCompat.setOnApplyWindowInsetsListener(web, (v, windowInsets) -> {
            Insets bars = windowInsets.getInsets(WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
            Insets ime = windowInsets.getInsets(WindowInsetsCompat.Type.ime());
            ViewGroup.MarginLayoutParams mlp = (ViewGroup.MarginLayoutParams) v.getLayoutParams();
            mlp.leftMargin = bars.left;
            mlp.topMargin = bars.top;
            mlp.rightMargin = bars.right;
            mlp.bottomMargin = Math.max(bars.bottom, ime.bottom);
            v.setLayoutParams(mlp);
            return WindowInsetsCompat.CONSUMED;
        });
        ViewCompat.requestApplyInsets(web);
        // «Назад»: сначала история WebView (внутри NA и обратно к списку серверов), потом выход.
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                WebView wv = getBridge() != null ? getBridge().getWebView() : null;
                if (wv != null && wv.canGoBack()) {
                    wv.goBack();
                } else {
                    setEnabled(false);
                    getOnBackPressedDispatcher().onBackPressed();
                }
            }
        });
    }
}
