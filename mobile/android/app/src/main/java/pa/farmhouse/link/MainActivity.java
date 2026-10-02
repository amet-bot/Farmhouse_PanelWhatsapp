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
    /** El botón "Recordar" de una tarea pendiente: sonido de urgencia, insistente (ver
     * services/fcm_service.py, CANAL_RECORDATORIOS). */
    public static final String CANAL_RECORDATORIOS = "recordatorios";

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        crearCanalDeAvisos();
        crearCanalDeTareas();
        crearCanalDeRecordatorios();
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

    /**
     * "Recordatorio" de una tarea que sigue pendiente: suena a urgencia (res/raw/notif_recordatorio.wav,
     * dos tonos alternados rápidos) y vibra largo, para que no pase desapercibido. Android no deja
     * cambiar el sonido de un canal ya creado: si algún día cambia este sonido, hay que usar un id
     * de canal nuevo.
     */
    private void crearCanalDeRecordatorios() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationChannel canal = new NotificationChannel(
            CANAL_RECORDATORIOS, "Recordatorios urgentes", NotificationManager.IMPORTANCE_HIGH);
        canal.setDescription("Cuando el encargado vuelve a avisar de una tarea que sigue pendiente.");
        canal.enableVibration(true);
        canal.setVibrationPattern(new long[] {0, 500, 150, 500, 150, 500, 400, 800});
        canal.enableLights(true);
        canal.setLightColor(0xFFDC2626);
        canal.setLockscreenVisibility(android.app.Notification.VISIBILITY_PUBLIC);
        canal.setSound(
            Uri.parse("android.resource://" + getPackageName() + "/raw/notif_recordatorio"),
            atributosDeSonido());
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) manager.createNotificationChannel(canal);
    }
}
