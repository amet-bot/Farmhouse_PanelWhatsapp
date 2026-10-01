package pa.farmhouse.link;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.media.AudioAttributes;
import android.media.RingtoneManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    /** El mismo id que manda el servidor en cada aviso (services/fcm_service.py). */
    public static final String CANAL_AVISOS = "avisos";
    /** Canal aparte para tareas: mismo nivel de importancia, pero con otro sonido — para que no
     * se confunda con un mensaje de WhatsApp de un cliente (ver services/fcm_service.py). */
    public static final String CANAL_TAREAS = "tareas";

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        crearCanalDeAvisos();
        crearCanalDeTareas();
    }

    private static AudioAttributes atributosDeSonido() {
        return new AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_NOTIFICATION)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
            .build();
    }

    /**
     * Canal de importancia alta: la notificación aparece arriba de la pantalla aunque se esté en
     * otra app, suena, vibra y se ve en la pantalla de bloqueo. Se crea al abrir la app (antes de
     * que llegue el primer aviso); si ya existe, Android lo deja como está.
     */
    private void crearCanalDeAvisos() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationChannel canal = new NotificationChannel(
            CANAL_AVISOS, "Mensajes de WhatsApp", NotificationManager.IMPORTANCE_HIGH);
        canal.setDescription("Mensajes nuevos de clientes por WhatsApp.");
        canal.enableVibration(true);
        canal.setVibrationPattern(new long[] {0, 250, 150, 250});
        canal.enableLights(true);
        canal.setLockscreenVisibility(android.app.Notification.VISIBILITY_PUBLIC);
        canal.setSound(RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION), atributosDeSonido());
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) manager.createNotificationChannel(canal);
    }

    /**
     * Mismo nivel de alerta que "avisos" (arriba de la pantalla, vibra, pantalla de bloqueo),
     * pero con un sonido propio (res/raw/notif_tarea.wav) para distinguirlo de un mensaje de
     * WhatsApp sin tener que mirar el teléfono. Usado para tareas: nuevas, reasignadas o hechas
     * (ver _notify_task en routers/ops.py).
     */
    private void crearCanalDeTareas() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationChannel canal = new NotificationChannel(
            CANAL_TAREAS, "Tareas", NotificationManager.IMPORTANCE_HIGH);
        canal.setDescription("Tareas nuevas, reasignadas o marcadas como hechas.");
        canal.enableVibration(true);
        canal.setVibrationPattern(new long[] {0, 180, 90, 180, 90, 180});
        canal.enableLights(true);
        canal.setLockscreenVisibility(android.app.Notification.VISIBILITY_PUBLIC);
        canal.setSound(
            Uri.parse("android.resource://" + getPackageName() + "/raw/notif_tarea"),
            atributosDeSonido());
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) manager.createNotificationChannel(canal);
    }
}
