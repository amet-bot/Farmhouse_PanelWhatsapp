package pa.farmhouse.link;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.media.AudioAttributes;
import android.media.RingtoneManager;
import android.os.Build;
import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    /** El mismo id que manda el servidor en cada aviso (services/fcm_service.py). */
    public static final String CANAL_AVISOS = "avisos";

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        crearCanalDeAvisos();
    }

    /**
     * Canal de importancia alta: la notificación aparece arriba de la pantalla aunque se esté en
     * otra app, suena, vibra y se ve en la pantalla de bloqueo. Se crea al abrir la app (antes de
     * que llegue el primer aviso); si ya existe, Android lo deja como está.
     */
    private void crearCanalDeAvisos() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationChannel canal = new NotificationChannel(
            CANAL_AVISOS, "Avisos de Farmhouse", NotificationManager.IMPORTANCE_HIGH);
        canal.setDescription("Cargamentos con diferencias, mermas importantes, cargamentos agendados y mensajes.");
        canal.enableVibration(true);
        canal.setVibrationPattern(new long[] {0, 250, 150, 250});
        canal.enableLights(true);
        canal.setLockscreenVisibility(android.app.Notification.VISIBILITY_PUBLIC);
        canal.setSound(
            RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION),
            new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_NOTIFICATION)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                .build());
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) manager.createNotificationChannel(canal);
    }
}
