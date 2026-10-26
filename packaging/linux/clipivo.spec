%global debug_package %{nil}
%global __strip /bin/true
%global __brp_python_bytecompile %{nil}
%global __brp_mangle_shebangs %{nil}
%global __brp_check_rpaths %{nil}
%global _build_id_links none

Name:           clipivo
Version:        %{clipivo_version}
Release:        1
Summary:        Find every appearance of a person in your videos and cut it out
License:        MIT
URL:            https://github.com/longweekendlabs/clipivo
Source0:        clipivo-%{version}-tree.tar.gz

BuildArch:      x86_64
# The application is a PyInstaller bundle with its own libraries; only what it uses from
# the system is listed. VLC plays video, ffmpeg saves clips.
AutoReqProv:    no
Requires:       libvlc.so.5()(64bit)
Requires:       /usr/bin/ffmpeg
Requires:       /usr/bin/ffprobe
Requires:       xcb-util-cursor
Requires:       libxkbcommon-x11
Requires:       mesa-libEGL
Requires:       mesa-libGL

%description
Clipivo finds every appearance of a person in your videos and cuts each one into a clip,
or follows them through split screens and studio shots. Face matching runs on your own
computer. The face models are downloaded on first launch.

%prep
%setup -q -c

%build

%install
cp -a . %{buildroot}/

%files
/opt/clipivo
%{_bindir}/clipivo
%{_datadir}/applications/io.longweekendlabs.Clipivo.desktop
%{_datadir}/icons/hicolor/512x512/apps/io.longweekendlabs.Clipivo.png
%{_datadir}/icons/hicolor/scalable/apps/io.longweekendlabs.Clipivo.svg
