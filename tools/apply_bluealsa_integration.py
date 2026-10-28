#!/usr/bin/env python3
"""Apply the BlueALSA integration to the current dap_daemon.py safely.

This script is intended for the Raspberry Pi checkout. It refuses to modify the
file if the expected source blocks are not found exactly once, creates a backup,
and is idempotent after a successful application.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "dap_daemon.py"
BACKUP = ROOT / "dap_daemon.py.bluealsa-backup"

IMPORT_MARKER = "from network_parse_utils import (\n"
IMPORT_BLOCK = '''from bluealsa_utils import (\n    build_bluealsa_asoundrc,\n    pcm_list_contains_device,\n)\n'''

HELPER_MARKER = "def is_bt_connected(mac):\n"
HELPERS = '''def is_bluealsa_active():\n    """BlueALSAサービスが起動中か確認する。"""\n    try:\n        result = subprocess.run(\n            ["systemctl", "is-active", "--quiet", "bluealsa"],\n            check=False,\n            timeout=5,\n        )\n        return result.returncode == 0\n    except (OSError, subprocess.SubprocessError):\n        return False\n\n\ndef get_bluealsa_pcm_list():\n    """bluealsa-aplayが認識しているBluetooth PCM一覧を返す。"""\n    try:\n        result = subprocess.run(\n            ["bluealsa-aplay", "-L"],\n            capture_output=True,\n            text=True,\n            errors="ignore",\n            timeout=5,\n            check=False,\n        )\n        if result.returncode != 0:\n            return ""\n        return result.stdout or ""\n    except (OSError, subprocess.SubprocessError):\n        return ""\n\n\ndef wait_for_bluealsa_pcm(mac, timeout=10.0):\n    """対象Bluetooth機器のA2DP PCMが公開されるまで待つ。"""\n    deadline = time.monotonic() + timeout\n    while time.monotonic() < deadline:\n        if pcm_list_contains_device(get_bluealsa_pcm_list(), mac):\n            return True\n        time.sleep(0.5)\n    return False\n\n\ndef write_text_atomically(path, content):\n    """設定ファイルを一時ファイル経由で安全に置き換える。"""\n    temp_path = f"{path}.tmp"\n    try:\n        with open(temp_path, "w", encoding="utf-8") as file:\n            file.write(content)\n            file.flush()\n            os.fsync(file.fileno())\n        os.replace(temp_path, path)\n    except Exception:\n        try:\n            if os.path.exists(temp_path):\n                os.remove(temp_path)\n        except OSError:\n            pass\n        raise\n\n\n'''

OLD_CONNECTED_BLOCK = '''    if connected:\n        config_content = f"""pcm.!default {{\n    type plug\n    slave.pcm "equal"\n}}\n\npcm.equal {{\n    type equal\n    slave.pcm {{\n        type bluealsa\n        device "{mac}"\n        profile "a2dp"\n    }}\n}}\nctl.equal {{\n    type equal\n}}\n"""\n        old_config = None\n        config_existed = os.path.exists(asoundrc_path)\n        try:\n            if config_existed:\n                with open(asoundrc_path, "r") as f:\n                    old_config = f.read()\n            with open(asoundrc_path, "w") as f:\n                f.write(config_content)\n            subprocess.run(["amixer", "-D", "bluealsa", "sset", "Master", "100%"], check=False)\n            audio_output_mode = "BT"\n            safe_init_audio()\n            apply_eq_settings()\n            status_message = "接続成功"\n        except Exception as e:\n            print(f"BT ALSA設定エラー: {e}")\n            audio_output_mode = previous_audio_mode\n            connected_bt_mac = previous_connected_mac\n            status_message = "音声設定失敗"\n            try:\n                if config_existed and old_config is not None:\n                    with open(asoundrc_path, "w") as f:\n                        f.write(old_config)\n                elif not config_existed and os.path.exists(asoundrc_path):\n                    os.remove(asoundrc_path)\n            except Exception as restore_error:\n                print(f"ALSA設定の復元エラー: {restore_error}")\n            if previous_audio_mode == "PWM":\n                restore_pwm_after_failure()\n    else:\n        status_message = "PIN/確認が必要です" if (not paired and requires_user_confirmation) else ("ペアリング失敗" if not paired else "接続失敗")\n        connected_bt_mac = previous_connected_mac\n        if previous_audio_mode == "PWM":\n            # 接続失敗時はPWMへ戻し、Wi-Fiを戻してSSH再接続を可能にする。\n            restore_pwm_after_failure()\n\n    request_display_update(is_full_refresh=True)\n    time.sleep(1.5)\n    status_message = ""\n    update_menu_items()\n    request_display_update(is_full_refresh=False)\n'''

NEW_CONNECTED_BLOCK = '''    if connected:\n        if not is_bluealsa_active():\n            subprocess.run(\n                ["sudo", "systemctl", "start", "bluealsa"],\n                check=False,\n                timeout=10,\n            )\n\n        if not is_bluealsa_active():\n            print("BlueALSAサービスを起動できません")\n            status_message = "BlueALSA起動失敗"\n            connected_bt_mac = previous_connected_mac\n            if previous_audio_mode == "PWM":\n                restore_pwm_after_failure()\n        else:\n            status_message = "音声デバイス待機中..."\n            request_display_update(is_full_refresh=False)\n\n            if not wait_for_bluealsa_pcm(mac, timeout=10.0):\n                print(f"BlueALSA PCMが見つかりません: {mac}")\n                status_message = "音声デバイス未検出"\n                connected_bt_mac = previous_connected_mac\n                if previous_audio_mode == "PWM":\n                    restore_pwm_after_failure()\n            else:\n                config_content = build_bluealsa_asoundrc(mac)\n                old_config = None\n                config_existed = os.path.exists(asoundrc_path)\n                try:\n                    if config_existed:\n                        with open(asoundrc_path, "r", encoding="utf-8") as file:\n                            old_config = file.read()\n                    write_text_atomically(asoundrc_path, config_content)\n                    if asound:\n                        try:\n                            asound.snd_config_update_free_global()\n                        except Exception as error:\n                            print(f"ALSA設定再読み込み警告: {error}")\n                    audio_output_mode = "BT"\n                    connected_bt_mac = mac\n                    safe_init_audio()\n                    if not pygame.mixer.get_init():\n                        raise RuntimeError("Pygame mixerの初期化に失敗しました")\n                    subprocess.run(\n                        ["amixer", "-D", "bluealsa", "sset", "Master", "100%"],\n                        check=False,\n                        timeout=5,\n                    )\n                    apply_eq_settings()\n                    status_message = "接続成功"\n                except Exception as error:\n                    print(f"BT ALSA設定エラー: {error}")\n                    audio_output_mode = previous_audio_mode\n                    connected_bt_mac = previous_connected_mac\n                    status_message = "音声設定失敗"\n                    try:\n                        if config_existed and old_config is not None:\n                            write_text_atomically(asoundrc_path, old_config)\n                        elif not config_existed and os.path.exists(asoundrc_path):\n                            os.remove(asoundrc_path)\n                    except Exception as restore_error:\n                        print(f"ALSA設定の復元エラー: {restore_error}")\n                    if previous_audio_mode == "PWM":\n                        restore_pwm_after_failure()\n    else:\n        status_message = "PIN/確認が必要です" if (not paired and requires_user_confirmation) else ("ペアリング失敗" if not paired else "接続失敗")\n        connected_bt_mac = previous_connected_mac\n        if previous_audio_mode == "PWM":\n            restore_pwm_after_failure()\n\n    request_display_update(is_full_refresh=True)\n    time.sleep(1.5)\n    status_message = ""\n    update_menu_items()\n    request_display_update(is_full_refresh=False)\n'''

OLD_PWM_PREFIX = '''    elif mode == "PWM":\n        # PWMへ戻す前にBlueALSAとBluetooth無線を停止する。\n        subprocess.run(["sudo", "systemctl", "stop", "bluealsa"], check=False)\n        subprocess.run(["sudo", "rfkill", "block", "bluetooth"], check=False)\n        time.sleep(0.3)\n'''

NEW_PWM_PREFIX = '''    elif mode == "PWM":\n        try:\n            if pygame.mixer.get_init():\n                pygame.mixer.music.stop()\n                pygame.mixer.quit()\n        except Exception as error:\n            print(f"Audio shutdown warning: {error}")\n\n        if asound:\n            try:\n                asound.snd_config_update_free_global()\n            except Exception:\n                pass\n\n        # PWMへ戻す前にBlueALSAとBluetooth無線を停止する。\n        subprocess.run(["sudo", "systemctl", "stop", "bluealsa"], check=False)\n        subprocess.run(["sudo", "rfkill", "block", "bluetooth"], check=False)\n        time.sleep(0.5)\n'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    if not TARGET.exists():
        raise SystemExit(f"not found: {TARGET}")
    source = TARGET.read_text(encoding="utf-8")
    if "def is_bluealsa_active():" in source:
        print("BlueALSA integration is already applied; nothing to do.")
        return

    updated = replace_once(source, IMPORT_MARKER, IMPORT_BLOCK + IMPORT_MARKER, "import insertion")
    updated = replace_once(updated, HELPER_MARKER, HELPERS + HELPER_MARKER, "helper insertion")
    updated = replace_once(updated, OLD_CONNECTED_BLOCK, NEW_CONNECTED_BLOCK, "Bluetooth connection block")
    updated = replace_once(updated, OLD_PWM_PREFIX, NEW_PWM_PREFIX, "PWM transition block")

    BACKUP.write_text(source, encoding="utf-8")
    TARGET.write_text(updated, encoding="utf-8")
    print(f"Applied BlueALSA integration to {TARGET}")
    print(f"Backup written to {BACKUP}")


if __name__ == "__main__":
    main()
